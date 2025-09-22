import grpc
from app.utils.jwt_utils import verify_jwt_token

class AuthInterceptor(grpc.ServerInterceptor):
    def __init__(self):
        # By default, all methods are public except Logout
        self.protected_methods = {
            '/auth.AuthService/Logout': True  # Only Logout requires authentication
        }

    def intercept_service(self, continuation, handler_call_details):
        method_name = handler_call_details.method
        
        # If method is not protected, proceed without authentication
        if method_name not in self.protected_methods:
            return continuation(handler_call_details)

        # For protected endpoints (like Logout), verify authentication
        metadata = dict(handler_call_details.invocation_metadata)
        auth_header = metadata.get('authorization', '')
        
        if not auth_header.startswith('Bearer '):
            return self._unauthenticated()

        token = auth_header[7:]  # Remove 'Bearer ' prefix
        payload, error = verify_jwt_token(token)
        
        if error:
            return self._unauthenticated()

        return continuation(handler_call_details)

    def _unauthenticated(self):
        def terminate(ignored_request, context):
            context.abort(grpc.StatusCode.UNAUTHENTICATED, 'Invalid or missing authentication token')
        return grpc.unary_unary_rpc_method_handler(terminate)