# encoding: utf-8

import json
from nose.tools import assert_equal, assert_not_equal, assert_raises

import sqlalchemy.orm as orm

import ckan.lib.create_test_data as ctd
import ckan.model as model
from ckan.tests import helpers
from ckan.plugins.toolkit import ValidationError
import ckan.tests.factories as factories
from ckan.logic import NotFound
import ckanext.datastore.backend.postgres as db
from ckanext.datastore.tests.helpers import (
    set_url_type, when_was_last_analyze, execute_sql,
    DatastoreFunctionalTestBase, DatastoreLegacyTestBase)


class TestDatastoreDelete(DatastoreFunctionalTestBase):
    def test_delete_basic(self):
        resource = factories.Resource()
        data = {
            'resource_id': resource['id'],
            'force': True,
            'aliases': u'b\xfck2',
            'fields': [{'id': 'book', 'type': 'text'},
                       {'id': 'author', 'type': 'text'},
                       {'id': 'rating with %', 'type': 'text'}],
            'records': [{'book': 'annakarenina', 'author': 'tolstoy',
                         'rating with %': '90%'},
                        {'book': 'warandpeace', 'author': 'tolstoy',
                         'rating with %': '42%'}]
        }
        helpers.call_action('datastore_create', **data)
        data = {
            'resource_id': resource['id'],
            'force': True,
        }
        helpers.call_action('datastore_delete', **data)

        results = execute_sql(u'select 1 from pg_views where viewname = %s', u'b\xfck2')
        assert results.rowcount == 0

        # check the table is gone
        results = execute_sql(
            u'''SELECT table_name
            FROM information_schema.tables
            WHERE table_name=%s;''',
            resource['id'])
        assert results.rowcount == 0

    def test_calculate_record_count_is_false(self):
        resource = factories.Resource()
        data = {
            'resource_id': resource['id'],
            'force': True,
            'fields': [{'id': 'name', 'type': 'text'},
                       {'id': 'age', 'type': 'text'}],
            'records': [{"name": "Sunita", "age": "51"},
                        {"name": "Bowan", "age": "68"}],
        }
        helpers.call_action('datastore_create', **data)
        data = {
            'resource_id': resource['id'],
            'filters': {'name': 'Bowan'},
            'force': True,
        }
        helpers.call_action('datastore_delete', **data)
        last_analyze = when_was_last_analyze(resource['id'])
        assert_equal(last_analyze, None)

    def test_calculate_record_count(self):
        resource = factories.Resource()
        data = {
            'resource_id': resource['id'],
            'force': True,
            'fields': [{'id': 'name', 'type': 'text'},
                       {'id': 'age', 'type': 'text'}],
            'records': [{"name": "Sunita", "age": "51"},
                        {"name": "Bowan", "age": "68"}],
        }
        helpers.call_action('datastore_create', **data)
        data = {
            'resource_id': resource['id'],
            'filters': {'name': 'Bowan'},
            'calculate_record_count': True,
            'force': True,
        }
        helpers.call_action('datastore_delete', **data)
        last_analyze = when_was_last_analyze(resource['id'])
        assert_not_equal(last_analyze, None)


class TestDatastoreDeleteLegacy(DatastoreLegacyTestBase):
    sysadmin_user = None
    normal_user = None
    Session = None

    @classmethod
    def setup_class(cls):
        cls.app = helpers._get_test_app()
        super(TestDatastoreDeleteLegacy, cls).setup_class()
        ctd.CreateTestData.create()
        cls.sysadmin_user = model.User.get('testsysadmin')
        cls.normal_user = model.User.get('annafan')
        resource = model.Package.get('annakarenina').resources[0]
        cls.data = {
            'resource_id': resource.id,
            'aliases': u'b\xfck2',
            'fields': [{'id': 'book', 'type': 'text'},
                       {'id': 'author', 'type': 'text'},
                       {'id': 'rating with %', 'type': 'text'}],
            'records': [{'book': 'annakarenina', 'author': 'tolstoy',
                         'rating with %': '90%'},
                        {'book': 'warandpeace', 'author': 'tolstoy',
                         'rating with %': '42%'}]
        }

        engine = db.get_write_engine()

        cls.Session = orm.scoped_session(orm.sessionmaker(bind=engine))
        set_url_type(
            model.Package.get('annakarenina').resources, cls.sysadmin_user)

    def _create(self):
        postparams = '%s=1' % json.dumps(self.data)
        auth = {'Authorization': str(self.sysadmin_user.apikey)}
        res = self.app.post('/api/action/datastore_create', params=postparams,
                            extra_environ=auth)
        res_dict = json.loads(res.body)
        assert res_dict['success'] is True
        return res_dict

    def _delete(self):
        data = {'resource_id': self.data['resource_id']}
        postparams = '%s=1' % json.dumps(data)
        auth = {'Authorization': str(self.sysadmin_user.apikey)}
        res = self.app.post('/api/action/datastore_delete', params=postparams,
                            extra_environ=auth)
        res_dict = json.loads(res.body)
        assert res_dict['success'] is True
        assert res_dict['result'] == data
        return res_dict

    def test_datastore_deleted_during_resource_deletion(self):
        package = factories.Dataset()
        data = {
            'resource': {
                'boo%k': 'crime',
                'author': ['tolstoy', 'dostoevsky'],
                'package_id': package['id']
            },
        }

        result = helpers.call_action('datastore_create', **data)
        resource_id = result['resource_id']
        helpers.call_action('resource_delete', id=resource_id)

        assert_raises(
            NotFound, helpers.call_action, 'datastore_search',
            resource_id=resource_id)

    def test_datastore_deleted_during_resource_only_for_deleted_resource(self):
        package = factories.Dataset()
        data = {
            'boo%k': 'crime',
            'author': ['tolstoy', 'dostoevsky'],
            'package_id': package['id']
        }

        result_1 = helpers.call_action(
            'datastore_create', resource=data.copy())
        resource_id_1 = result_1['resource_id']

        result_2 = helpers.call_action(
            'datastore_create', resource=data.copy())
        resource_id_2 = result_2['resource_id']

        res_1 = model.Resource.get(resource_id_1)
        res_2 = model.Resource.get(resource_id_2)

        # `synchronize_session=False` and session cache requires
        # refreshing objects
        model.Session.refresh(res_1)
        model.Session.refresh(res_2)
        assert res_1.extras['datastore_active']
        assert res_2.extras['datastore_active']

        helpers.call_action('resource_delete', id=resource_id_1)

        assert_raises(
            NotFound, helpers.call_action, 'datastore_search',
            resource_id=resource_id_1)
        assert_raises(
            NotFound, helpers.call_action, 'resource_show',
            id=resource_id_1)
        model.Session.refresh(res_1)
        model.Session.refresh(res_2)
        assert not res_1.extras['datastore_active']
        assert res_2.extras['datastore_active']

    def test_delete_invalid_resource_id(self):
        postparams = '%s=1' % json.dumps({'resource_id': 'bad'})
        auth = {'Authorization': str(self.sysadmin_user.apikey)}
        res = self.app.post('/api/action/datastore_delete', params=postparams,
                            extra_environ=auth, status=404)
        res_dict = json.loads(res.body)
        assert res_dict['success'] is False

    def test_delete_filters(self):
        self._create()
        resource_id = self.data['resource_id']

        # try and delete just the 'warandpeace' row
        data = {'resource_id': resource_id,
                'filters': {'book': 'warandpeace'}}
        postparams = '%s=1' % json.dumps(data)
        auth = {'Authorization': str(self.sysadmin_user.apikey)}
        res = self.app.post('/api/action/datastore_delete', params=postparams,
                            extra_environ=auth)
        res_dict = json.loads(res.body)
        assert res_dict['success'] is True

        c = self.Session.connection()
        result = c.execute(u'select * from "{0}";'.format(resource_id))
        results = [r for r in result]
        assert len(results) == 1
        assert results[0].book == 'annakarenina'
        self.Session.remove()

        # shouldn't delete anything
        data = {'resource_id': resource_id,
                'filters': {'book': 'annakarenina', 'author': 'bad'}}
        postparams = '%s=1' % json.dumps(data)
        auth = {'Authorization': str(self.sysadmin_user.apikey)}
        res = self.app.post('/api/action/datastore_delete', params=postparams,
                            extra_environ=auth)
        res_dict = json.loads(res.body)
        assert res_dict['success'] is True

        c = self.Session.connection()
        result = c.execute(u'select * from "{0}";'.format(resource_id))
        results = [r for r in result]
        assert len(results) == 1
        assert results[0].book == 'annakarenina'
        self.Session.remove()

        # delete the 'annakarenina' row and also only use id
        data = {'id': resource_id,
                'filters': {'book': 'annakarenina', 'author': 'tolstoy'}}
        postparams = '%s=1' % json.dumps(data)
        auth = {'Authorization': str(self.sysadmin_user.apikey)}
        res = self.app.post('/api/action/datastore_delete', params=postparams,
                            extra_environ=auth)
        res_dict = json.loads(res.body)
        assert res_dict['success'] is True

        c = self.Session.connection()
        result = c.execute(u'select * from "{0}";'.format(resource_id))
        results = [r for r in result]
        assert len(results) == 0
        self.Session.remove()

        self._delete()

    def test_delete_is_unsuccessful_when_called_with_invalid_filters(self):
        self._create()

        data = {
            'resource_id': self.data['resource_id'],
            'filters': {
                'invalid-column-name': 'value'
            }
        }
        postparams = '%s=1' % json.dumps(data)
        auth = {'Authorization': str(self.normal_user.apikey)}
        res = self.app.post('/api/action/datastore_delete', params=postparams,
                            extra_environ=auth, status=409)
        res_dict = json.loads(res.body)
        assert res_dict['success'] is False
        assert res_dict['error'].get('filters') is not None, res_dict['error']

        self._delete()

    def test_delete_is_unsuccessful_when_called_with_filters_not_as_dict(self):
        self._create()

        data = {
            'resource_id': self.data['resource_id'],
            'filters': []
        }
        postparams = '%s=1' % json.dumps(data)
        auth = {'Authorization': str(self.normal_user.apikey)}
        res = self.app.post('/api/action/datastore_delete', params=postparams,
                            extra_environ=auth, status=409)
        res_dict = json.loads(res.body)
        assert res_dict['success'] is False
        assert res_dict['error'].get('filters') is not None, res_dict['error']

        self._delete()

    def test_delete_with_blank_filters(self):
        self._create()

        res = self.app.post(
            '/api/action/datastore_delete',
            params='{0}=1'.format(
                json.dumps({
                    'resource_id': self.data['resource_id'],
                    'filters': {}
                })
            ),
            extra_environ={
                'Authorization': str(self.normal_user.apikey)
            },
            status=200
        )

        results = json.loads(res.body)
        assert(results['success'] is True)

        res = self.app.post(
            '/api/action/datastore_search',
            params='{0}=1'.format(
                json.dumps({
                    'resource_id': self.data['resource_id'],
                })
            ),
            extra_environ={
                'Authorization': str(self.normal_user.apikey)
            },
            status=200
        )

        results = json.loads(res.body)
        assert(results['success'] is True)
        assert(len(results['result']['records']) == 0)

        self._delete()


class TestDatastoreFunctionDelete(DatastoreFunctionalTestBase):
    def test_create_delete(self):
        helpers.call_action(
            u'datastore_function_create',
            name=u'test_nop',
            rettype=u'trigger',
            definition=u'BEGIN RETURN NEW; END;')
        helpers.call_action(
            u'datastore_function_delete',
            name=u'test_nop')

    def test_delete_nonexistant(self):
        try:
            helpers.call_action(
                u'datastore_function_delete',
                name=u'test_not_there')
        except ValidationError as ve:
            assert_equal(
                ve.error_dict,
                {u'name': [u'function test_not_there() does not exist']})
        else:
            assert 0, u'no validation error'

    def test_delete_if_exists(self):
        helpers.call_action(
            u'datastore_function_delete',
            name=u'test_not_there_either',
            if_exists=True)
