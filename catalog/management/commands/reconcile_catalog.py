import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from catalog.reconciliation import apply_plan, build_plan


class Command(BaseCommand):
    help = 'Plan reversible duplicate reconciliation; writes only with --apply and a matching plan hash.'

    def add_arguments(self, parser):
        parser.add_argument('--output', default='artifacts/catalog-reconciliation-plan.json')
        parser.add_argument('--pairs', help='JSON array of [duplicate UUID, canonical UUID] mappings; evidence checks still apply.')
        parser.add_argument('--plan', help='Previously saved plan JSON (required for apply).')
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--expect-hash')
        parser.add_argument('--rollback-check', action='store_true', help='Execute the guarded plan and roll back all changes.')
        parser.add_argument('--no-assets', action='store_true')

    def handle(self, *args, **options):
        try:
            if options['apply'] or options['rollback_check']:
                if not options['plan'] or not options['expect_hash']:
                    raise CommandError('--plan and --expect-hash are required for apply/rollback-check.')
                if options['pairs']:
                    raise CommandError('--pairs cannot be combined with apply.')
                plan=json.loads(Path(options['plan']).read_text())
                report=apply_plan(plan,expected_hash=options['expect_hash'],rollback=not options['apply'])
            else:
                pairs=json.loads(Path(options['pairs']).read_text()) if options['pairs'] else None
                plan=build_plan(recording_pairs=pairs,include_assets=not options['no_assets'])
                output=Path(options['output']); output.parent.mkdir(parents=True,exist_ok=True)
                output.write_text(json.dumps(plan,indent=2,default=str)+'\n')
                report={'mode':'dry-run','plan_path':str(output.resolve()),'plan_hash':plan['plan_hash'],
                    'recording_merges':len(plan['recording_merges']),'asset_merges':len(plan['asset_merges']),
                    'actions':len(plan['actions']),'conflicts':len(plan['conflicts']),'pending':len(plan['pending'])}
            self.stdout.write(json.dumps(report,sort_keys=True))
        except (ValueError,OSError) as exc:
            raise CommandError(str(exc)) from exc
