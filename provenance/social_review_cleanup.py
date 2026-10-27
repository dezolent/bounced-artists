"""Retire artist-only social observation notices without deleting evidence."""
from django.db import connection, transaction
from django.db.models import Count, F, Q

from catalog.reconciliation import digest
from provenance.models import AuditEvent, FieldObservation, ReviewItem


def plan_social_reviews():
    eligible = ReviewItem.objects.filter(status='open', issue_type='platform_object_discovered').filter(
        Q(source_record__service__slug='tiktok', source_record__object_type='tiktok-video') |
        Q(source_record__service__slug='instagram', source_record__object_type__in=['post-details', 'post-list-item'])
    ).exclude(source_record_id__in=FieldObservation.objects.values('source_record_id')).annotate(
        candidate_count=Count('candidates', distinct=True)).filter(candidate_count=1,
        candidates__target__artist__isnull=False,
        candidates__target__organization_id=F('source_record__organization_id'),
        candidates__target__artist__organization_id=F('source_record__organization_id'))
    rows = list(eligible.order_by('pk').values('id', 'updated_at', 'source_record_id',
        'source_record__organization_id', 'source_record__updated_at', 'candidates__target_id', 'candidates__target__updated_at'))
    plan = {'count': len(rows), 'rows': rows}
    plan['hash'] = digest(plan)
    return plan


@transaction.atomic
def apply_social_reviews(plan, *, rollback=False):
    if connection.vendor != 'postgresql':
        raise ValueError('PostgreSQL locking is required')
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL lock_timeout = '5s'")
        for table in ('provenance_reviewitem', 'provenance_reviewcandidate', 'provenance_entitytarget',
                      'provenance_sourcerecord', 'provenance_fieldobservation'):
            cursor.execute('LOCK TABLE ' + connection.ops.quote_name(table) + ' IN SHARE ROW EXCLUSIVE MODE')
    if plan_social_reviews()['hash'] != plan.get('hash'):
        raise ValueError('Review state changed; generate a new plan')
    rows = plan['rows']
    by_org = {}
    for row in rows:
        by_org.setdefault(str(row['source_record__organization_id']), []).append(str(row['id']))
    changed = ReviewItem.objects.filter(pk__in=[row['id'] for row in rows], status='open').update(status='dismissed')
    if changed != plan['count']:
        raise ValueError('Review state changed during apply')
    for org, ids in by_org.items():
        AuditEvent.objects.create(organization_id=org, action='review.dismiss_social_discovery', target_label='Social observation notices',
            change_json={'plan_hash': plan['hash'], 'review_ids': ids, 'reason': 'Social observations are retained without recording review'})
    if rollback:
        transaction.set_rollback(True)
    return {'dismissed': changed, 'plan_hash': plan['hash'], 'rolled_back': rollback}
