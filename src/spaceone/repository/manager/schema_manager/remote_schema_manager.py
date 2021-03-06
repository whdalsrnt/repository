import consul
import logging
import time

from spaceone.core import config
from spaceone.core.auth.jwt.jwt_util import JWTUtil
from spaceone.core.connector.space_connector import SpaceConnector
from spaceone.repository.manager.schema_manager import SchemaManager

__all__ = ['RemoteSchemaManager']

_LOGGER = logging.getLogger(__name__)
_INTERVAL = 10


def _validate_token(token):
    if isinstance(token, dict):
        protocol = token['protocol']
        if protocol == 'consul':
            consul_instance = Consul(token['config'])
            value = False
            count = 0
            while value is False:
                value = consul_instance.patch_token(token['uri'])
                _LOGGER.warn(f'[_validate_token] token: {value}')
                if value:
                    break
                time.sleep(_INTERVAL)

            token = value

    _LOGGER.debug(f'[_validate_token] token: {token}')
    return token


class RemoteSchemaManager(SchemaManager):
    """
    self.repository (=repository_vo)
    Remote Schema make gRPC call to remote repository (like marketplace)
    If connector gets schema_info, this is gRPC message.
    """

    def get_schema(self, schema_id, domain_id, only=None):
        """
        Args:
            - schema_id
            - domain_id : my domain_id
        """
        conn = self._get_conn_from_repository(self.repository, domain_id)
        connector = self.locator.get_connector('RemoteRepositoryConnector', conn=conn)

        # schema_info, dict
        schema_info = connector.get_schema(schema_id, only)
        return self._get_updated_schema_info(schema_info)        

    def list_schemas(self, query, domain_id):
        conn = self._get_conn_from_repository(self.repository, domain_id)
        connector = self.locator.get_connector('RemoteRepositoryConnector', conn=conn)

        # Notice:
        # query should be JSON style query, not gRPC
        #

        response = connector.list_schemas(query)
        _LOGGER.debug(f'[remote list_schema] count: {response.total_count}')

        for schema_info in response.results:
            # Warning:
            # This is side effect coding, since schema_vo is protobuf message
            self._get_updated_schema_info(schema_info)  
        return response.results, response.total_count

    def stat_schemas(self, query, domain_id):
        raise NotImplementedError('Remote repository is not supported.')

    def _get_conn_from_repository(self, repo, domain_id):
        """
        self.repository (repository_vo)

        Args:
            - repo: repository_vo (= self.repository)
            - domain_id: domain_id of credential
        """
        cred_id = repo.secret_id
        credentials = self._get_secret_data(cred_id, domain_id)
        conn = {
            'endpoint': repo.endpoint,
            'version': repo.version,
            'credential': {'token': credentials['token']}
        }
        return conn

    def _get_updated_schema_info(self, schema_info):
        """
        schema_info is Protobuf Message
        We want to change our schema_info (especially repository_info)

        Args:
            - schema_info: protobuf message
        """
        # domain_id is remote repository's domain_id
        # change to local repository's domain_id  
        # There is no way to find domain_id
        # TODO: schema_info.domain_id = self.repository.domain_id

        schema_info.repository_info.name = self.repository.name
        schema_info.repository_info.repository_type = self.repository.repository_type
        return schema_info

    ###############################
    # Credential/CredentialGroup
    ###############################
    def _get_secret_data(self, secret_id, domain_id):
        """ Return secret data
        """
        root_token = config.get_global('ROOT_TOKEN')
        root_token_info = config.get_global('ROOT_TOKEN_INFO')

        root_domain_id = domain_id
        if root_token != "":
            root_domain_id = self._get_domain_id_from_token(root_token)
            _LOGGER.debug(f'[_get_secret_data] root_domain_id: {root_domain_id} vs domain_id: {domain_id}')
        elif root_token_info:
            # Patch from Consul
            _LOGGER.debug(f'[_get_secret_data] Patch root_token from Consul')
            root_token = _validate_token(root_token_info)
            root_domain_id = self._get_domain_id_from_token(root_token)
        else:
            _LOGGER.warn(f'[_get_secret_data] root_token is not configured, may be your are root')
            root_token = self.transaction.get_meta('token')

        secret_connector: SpaceConnector = self.locator.get_connector('SpaceConnector', service='secret',
                                                                      token=root_token)
        secret_data = secret_connector.dispatch('Secret.get_data',
                                                {'secret_id': secret_id, 'domain_id': root_domain_id})
        return secret_data['data']

    def _get_domain_id_from_token(self, token):
        decoded_token = JWTUtil.unverified_decode(token)
        return decoded_token['did']


class Consul:
    def __init__(self, config):
        """
        Args:
          - config: connection parameter

        Example:
            config = {
                    'host': 'consul.example.com',
                    'port': 8500
                }
        """
        self.config = self._validate_config(config)

    def _validate_config(self, config):
        """
        Parameter for Consul
        - host, port=8500, token=None, scheme=http, consistency=default, dc=None, verify=True, cert=None
        """
        options = ['host', 'port', 'token', 'scheme', 'consistency', 'dc', 'verify', 'cert']
        result = {}
        for item in options:
            value = config.get(item, None)
            if value:
              result[item] = value
        return result

    def patch_token(self, key):
        """
        Args:
            key: Query key (ex. /debug/supervisor/TOKEN)

        """
        try:
            conn = consul.Consul(**self.config)
            index, data = conn.kv.get(key)
            return data['Value'].decode('ascii')

        except Exception as e:
            return False
