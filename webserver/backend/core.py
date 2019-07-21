import os
from datetime import datetime
from pathlib import Path

from code_execution import run_transformations
from flask import Flask, jsonify, request
from flask_cors import CORS
from reflection import get_modules_in_package, package_tree, package_tree_from_path, user_defined_modules, \
    load_module_from_path
from responses import transform_response
from typhoon import connections, variables
from typhoon.cli import _build_dags
from typhoon.connections import scan_connections, ConnectionParams, get_connection_local, \
    get_connections_local_by_conn_id
from typhoon.contrib.hooks import hook_factory
from typhoon.deployment.dags import get_dag_filenames
from typhoon.settings import typhoon_directory
from typhoon.variables import scan_variables, VariableType

app = Flask(__name__)
CORS(app)


@app.route('/build-dags')
def do_build_dags():
    env = request.args.get('env')
    _build_dags(target_env=env, debug=True)
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
        'functions': user_defined_modules(os.path.join(typhoon_directory(), 'functions')),
        'transformations': user_defined_modules(os.path.join(typhoon_directory(), 'transformations')),
    }
    return jsonify(modules)


@app.route('/typhoon-user-defined-package-trees')
def get_typhoon_user_defined_package_trees():
    package_trees = {
        'functions': package_tree_from_path(os.path.join(typhoon_directory(), 'functions')),
        'transformations': package_tree_from_path(os.path.join(typhoon_directory(), 'transformations')),
    }
    return jsonify(package_trees)


# noinspection PyUnresolvedReferences
@app.route('/run-transformations', methods=['POST'])
def get_run_transformations_result():
    from pandas import DataFrame    # Do not remove import so it can be used in eval
    from mock import Mock

    # Do not remove import so it can be used in eval
    # noinspection PyUnusedLocal,PyPep8Naming
    class Obj:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

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
    env = request.args.get('env')
    all_connections = scan_connections(env)
    return jsonify(all_connections)


@app.route('/connection-envs')
def get_connections_envs():
    conn_id = request.args.get('conn_id')
    all_connections = get_connections_local_by_conn_id(conn_id)
    all_connections = [{'conn_id': conn_id, 'conn_env': k, 'conn_type': v['conn_type']} for k, v in all_connections.items()]
    return jsonify(all_connections)


@app.route('/connection', methods=['PUT', 'DELETE'])
def set_connection():
    env = request.args.get('env')
    if request.method == 'PUT':
        body = request.get_json()
        conn_id = body.pop('conn_id')
        conn_params = ConnectionParams(**body)
        connections.set_connection(conn_id, conn_params, use_cli_config=False, target_env=env)
    else:   # Delete
        env = request.args.get('env')
        conn_id = request.args.get('conn_id')
        connections.delete_connection(conn_id, use_cli_config=True, target_env=env)
        connections.delete_connection(env, conn_id)
    return 'Ok'


@app.route('/swap-connection', methods=['PUT'])
def swap_connection():
    conn_id = request.args.get('conn_id')
    conn_env = request.args.get('conn_env')
    env = request.args.get('env')

    conn_params = get_connection_local(conn_id, conn_env)
    connections.set_connection(conn_id, conn_params, use_cli_config=True, target_env=env)
    return 'Ok'


@app.route('/connection-types')
def get_connection_types():
    typhoon_conn_types = set(hook_factory.HOOK_MAPPINGS.keys())
    custom_conn_factory_module = load_module_from_path(
        os.path.join(typhoon_directory(), 'hooks', 'hook_factory.py'), must_exist=False)
    custom_conn_types = set(custom_conn_factory_module.HOOK_MAPPINGS.keys()) if custom_conn_factory_module else set()
    conn_types = list(typhoon_conn_types.union(custom_conn_types))
    return jsonify(sorted(conn_types))


@app.route('/variables')
def get_variables():
    env = request.args.get('env')
    all_variables = scan_variables(to_dict=True, use_cli_config=True, target_env=env)
    return jsonify(all_variables)


@app.route('/variable', methods=['PUT', 'DELETE'])
def update_variable():
    env = request.args.get('env')
    if request.method == 'PUT':
        body = request.get_json()
        body['type'] = variables.VariableType(body['type'])
        variable = variables.Variable(**body)
        variables.set_variable(variable=variable, use_cli_config=True, target_env=env)
    else:   # Delete
        env = request.args.get('env')
        variable_id = request.args.get('id')
        variables.delete_variable(variable_id, use_cli_config=True, target_env=env)
    return 'Ok'


@app.route('/variable-types')
def get_variable_types():
    typhoon_var_types = [x.value for x in VariableType]
    return jsonify(sorted(typhoon_var_types))


@app.route('/get-dag-filenames')
def api_get_dag_filenames():
    dag_files = get_dag_filenames()
    return jsonify(sorted(dag_files))


@app.route('/get-dag-contents')
def api_get_dag_contents():
    filename = request.args.get('filename')
    filepath = Path(typhoon_directory()) / 'dags' / filename
    return jsonify({'contents': filepath.read_text()})
