"""Provider settings and persistent signing key, separate from application secrets."""
import os
from pathlib import Path
import time

from jwcrypto import jwk


def signing_key(local_dir):
    path = Path(os.environ.get('OIDC_PRIVATE_KEY_FILE', str(Path(local_dir) / 'oidc' / 'private.pem')))
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        private = jwk.JWK.generate(kty='RSA', size=2048).export_to_pem(private_key=True, password=None)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(private)
                stream.flush()
                os.fsync(stream.fileno())
    # A second worker may observe a just-created key before the first has flushed it.
    for _ in range(50):
        data = path.read_bytes()
        if data:
            jwk.JWK.from_pem(data)
            if os.name != 'nt':
                path.chmod(0o600)
            return data.decode('ascii')
        time.sleep(.05)
    raise RuntimeError('OIDC signing key is empty; restore its persistent backup before startup.')


def oauth_settings(local_dir, debug):
    return {
        'OIDC_ENABLED': True,
        'OIDC_RSA_PRIVATE_KEY': signing_key(local_dir),
        'OIDC_ISS_ENDPOINT': os.environ.get('OIDC_ISSUER', ''),
        'OAUTH2_VALIDATOR_CLASS': 'identity.validator.FederationValidator',
        'SCOPES': {'openid': 'Идентификатор ФСП ID', 'profile': 'Имя и фамилия',
                   'fsp': 'Роли, дисциплины и действующий разряд', 'email': 'Электронная почта'},
        'DEFAULT_SCOPES': ['openid'],
        'PKCE_REQUIRED': True,
        'ACCESS_TOKEN_EXPIRE_SECONDS': 3600,
        'ID_TOKEN_EXPIRE_SECONDS': 600,
        'AUTHORIZATION_CODE_EXPIRE_SECONDS': 120,
        'REFRESH_TOKEN_EXPIRE_SECONDS': 86400,
        'ROTATE_REFRESH_TOKEN': True,
        'REFRESH_TOKEN_REUSE_PROTECTION': True,
        'OIDC_RESPONSE_TYPES_SUPPORTED': ['code'],
        'OAUTH2_RESPONSE_TYPES_SUPPORTED': ['code'],
        'OAUTH2_GRANT_TYPES_SUPPORTED': ['authorization_code', 'refresh_token'],
        'OIDC_TOKEN_ENDPOINT_AUTH_METHODS_SUPPORTED': ['none'],
        'OAUTH2_TOKEN_ENDPOINT_AUTH_METHODS_SUPPORTED': ['none'],
        # Validation below permits HTTP only for the reserved local demonstration client.
        'ALLOWED_REDIRECT_URI_SCHEMES': ['https', 'http'] if debug else ['https'],
        'ALLOW_URI_WILDCARDS': False,
        'REQUEST_APPROVAL_PROMPT': 'force',
        'COMPLIANT_BCP_RFC9700_PKCE_METHOD': True,
        'COMPLIANT_BCP_RFC9700_IMPLICIT_GRANT': True,
        'COMPLIANT_BCP_RFC9700_PASSWORD_GRANT': True,
        'COMPLIANT_BCP_RFC9700_ACCESS_TOKEN_TRANSPORT': True,
        'COMPLIANT_BCP_RFC9700_AUTHZ_RESPONSE_ISS': True,
        'COMPLIANT_BCP_RFC9700_TOKEN_STORAGE': True,
        'DCR_ENABLED': False,
        'CIMD_ENABLED': False,
    }
