class PrivateWorkspaceHeaders:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path == '/' or request.path.startswith(('/app/', '/api/portal/', '/logout/')):
            response['Cache-Control'] = 'no-store, private'
            response['Vary'] = ', '.join(dict.fromkeys(filter(None, [response.get('Vary'), 'Cookie'])))
            response['Referrer-Policy'] = 'same-origin'
            response['Content-Security-Policy'] = "default-src 'self'; img-src 'self' https: data:; script-src 'self'; style-src 'self'; font-src 'self'; connect-src 'self'; media-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
        return response
