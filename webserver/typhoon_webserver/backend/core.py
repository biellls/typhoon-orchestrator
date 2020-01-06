import importlib.util
import inspect
from datetime import datetime

from flask import Flask, jsonify, request
from flask_cors import CORS

from code_execution import run_transformations
from reflection import get_modules_in_package, package_tree, package_tree_from_path, user_defined_modules
from responses import transform_response
from typhoon import variables
from typhoon.connections import get_connection_local, \
    get_connections_local_by_conn_id, Connection
from typhoon.contrib.hooks import hook_factory
from typhoon.core.glue import get_dag_filenames
from typhoon.core.settings import Settings
from typhoon.deployment.packaging import build_all_dags
from typhoon.handler import run_dag
from typhoon.remotes import Remotes
from typhoon.variables import VariableType

app = Flask(__name__)
CORS(app)


@app.route('/build-dags')
def do_build_dags():
    # TODO: Remove env from args
    build_all_dags(remote=None)
    return 'Ok'


@app.route('/typhoon-modules')
def get_typhoon_modules():
    modules = {
        'functions': get_modules_in_package('typhoon.contrib.functions'),
        'transformations': get_modules_in_package('typhoon.contrib.transformations'),
    }
    return jsonify(modules)


@app.route('/typhoon-package-trees')
def get_typhoon_package_trees():
    package_trees = {
        'functions': package_tree('typhoon.contrib.functions'),
        'transformations': package_tree('typhoon.contrib.transformations'),
    }
    return jsonify(package_trees)


@app.route('/typhoon-user-defined-modules')
def get_user_defined_modules():
    modules = {
        'functions': user_defined_modules(str(Settings.typhoon_home / 'functions')),
        'transformations': user_defined_modules(str(Settings.typhoon_home / 'transformations')),
    }
    return jsonify(modules)


@app.route('/typhoon-user-defined-package-trees')
def get_typhoon_user_defined_package_trees():
    package_trees = {
        'functions': package_tree_from_path(str(Settings.typhoon_home / 'functions')),
        'transformations': package_tree_from_path(str(Settings.typhoon_home / 'transformations')),
    }
    return jsonify(package_trees)


# Do not remove import so it can be used in eval
# noinspection PyUnusedLocal,PyPep8Naming
class Obj:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


# noinspection PyUnresolvedReferences
@app.route('/run-transformations', methods=['POST'])
def get_run_transformations_result():
    from pandas import DataFrame    # Do not remove import so it can be used in eval
    from mock import Mock

    body = request.get_json()
    response = {}
    source_data = eval(body['source']) if body['eval_source'] else body['source']
    dag_context = {
        'execution_date': datetime.strptime(body['dag_context']['execution_date'], '%Y-%m-%dT%H:%M'),
        'etl_timestamp': datetime.now(),
        'ts': body['dag_context']['execution_date'],
        'ds': body['dag_context']['execution_date'].split('T')[0],
        'ds_nodash': body['dag_context']['execution_date'].split('T')[0].replace('-', ''),
        'dag_name': body['dag_context']['dag_name'],
    }
    for param_name, param in body['edge'].items():
        if param['apply']:
            response[param_name] = run_transformations(
                source_data=source_data,
                dag_context=dag_context,
                user_transformations=param['contents']
            )

    response = transform_response(response)
    return jsonify(response)


@app.route('/connections')
def get_connections():
    remote = request.args.get('env')
    remote = None
    all_connections = Settings.metadata_store(Remotes.aws_profile(remote)).get_connections(to_dict=True)
    return jsonify(all_connections)


@app.route('/connection-envs')
def get_connections_envs():
    conn_id = request.args.get('conn_id')
    all_connections = get_connections_local_by_conn_id(conn_id)
    all_connections = [{'conn_id': conn_id, 'conn_env': k, 'conn_type': v['conn_type']} for k, v in all_connections.items()]
    return jsonify(all_connections)


@app.route('/connection', methods=['PUT', 'DELETE'])
def set_connection():
    remote = request.args.get('env')
    remote = None
    if request.method == 'PUT':
        body = request.get_json()
        conn = Connection(**body)
        Settings.metadata_store(Remotes.aws_profile(remote)).set_connection(conn)
    else:   # Delete
        env = request.args.get('env')
        conn_id = request.args.get('conn_id')
        Settings.metadata_store(Remotes.aws_profile(remote)).delete_connection(conn_id)
    return 'Ok'


@app.route('/swap-connection', methods=['PUT'])
def swap_connection():
    conn_id = request.args.get('conn_id')
    conn_env = request.args.get('conn_env')
    remote = request.args.get('env')
    remote = None

    conn_params = get_connection_local(conn_id, conn_env)
    Settings.metadata_store(Remotes.aws_profile(remote)).set_connection(conn_params.to_conn(conn_id))
    return 'Ok'


@app.route('/connection-types')
def get_connection_types():
    typhoon_conn_types = set(hook_factory.HOOK_MAPPINGS.keys())
    custom_conn_types = set()
    hooks_files = (Settings.typhoon_home / 'hooks').rglob('*.py')
    for hooks_file in hooks_files:
        spec = importlib.util.spec_from_file_location(str(hooks_file).split('.py')[0], str(hooks_file))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for cls_name, cls in inspect.getmembers(mod, inspect.isclass):
            conn_type = getattr(cls, 'conn_type', None)
            if conn_type:
                custom_conn_types.add(conn_type)
    conn_types = list(typhoon_conn_types.union(custom_conn_types))
    return jsonify(sorted(conn_types))


@app.route('/variables')
def get_variables():
    remote = request.args.get('env')
    remote = None
    all_variables = Settings.metadata_store(Remotes.aws_profile(remote)).get_variables(to_dict=True)
    return jsonify(all_variables)


@app.route('/variable', methods=['PUT', 'DELETE'])
def update_variable():
    remote = request.args.get('env')
    remote = None
    if request.method == 'PUT':
        body = request.get_json()
        body['type'] = variables.VariableType(body['type'])
        variable = variables.Variable(**body)
        Settings.metadata_store(Remotes.aws_profile(remote)).set_variable(variable)
    else:   # Delete
        remote = request.args.get('env')
        remote = None
        variable_id = request.args.get('id')
        Settings.metadata_store(Remotes.aws_profile(remote)).delete_variable(variable_id)
    return 'Ok'


@app.route('/variable-types')
def get_variable_types():
    typhoon_var_types = [x for x in VariableType]
    return jsonify(sorted(typhoon_var_types))


@app.route('/get-dag-filenames')
def api_get_dag_filenames():
    dag_files = get_dag_filenames()
    return jsonify(sorted(dag_files))


@app.route('/get-dag-contents')
def api_get_dag_contents():
    filename = request.args.get('filename')
    filepath = Settings.typhoon_home / 'dags' / filename
    return jsonify({'contents': filepath.read_text()})


@app.route('/save-dag-code', methods=['PUT'])
def api_save_dag_code():
    filename = request.args.get('filename')
    body = request.get_json()
    filepath = Settings.typhoon_home / 'dags' / filename
    filepath.write_text(body['code'])
    return 'Ok'


@app.route('/run-dag')
def api_run_dag():
    remote = request.args.get('env')
    remote = None
    build_all_dags(remote=remote)
    dag_name = request.args.get('dag_name')
    time = request.args.get('time')
    logs = run_dag(dag_name, time, capture_logs=True)
    return logs
