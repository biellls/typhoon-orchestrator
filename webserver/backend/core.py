import os
from datetime import datetime

from code_execution import run_transformations
from flask import Flask, jsonify, request
from flask_cors import CORS
from reflection import get_modules_in_package, package_tree, package_tree_from_path, user_defined_modules
from responses import transform_response
from typhoon import connections
from typhoon.connections import scan_connections, ConnectionParams
from typhoon.settings import typhoon_directory

app = Flask(__name__)
CORS(app)


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
    dag_config = {
        'execution_date': datetime.strptime(body['dag_config']['execution_date'], '%Y-%m-%dT%H:%M'),
        'etl_timestamp': datetime.now(),
        'ts': body['dag_config']['execution_date'],
        'ds': body['dag_config']['execution_date'].split('T')[0],
        'ds_nodash': body['dag_config']['execution_date'].split('T')[0].replace('-', ''),
        'dag_name': body['dag_config']['dag_name'],
    }
    for param_name, param in body['edge'].items():
        if param['apply']:
            response[param_name] = run_transformations(
                source_data=source_data,
                dag_config=dag_config,
                transformations=param['contents']
            )

    response = transform_response(response)
    return jsonify(response)


@app.route('/connections')
def get_connections():
    env = request.args.get('env')
    connections = scan_connections(env)
    return jsonify(connections)


@app.route('/connection', methods=['PUT'])
def set_connection():
    body = request.get_json()
    env = request.args.get('env')
    conn_id = body.pop('conn_id')
    conn_params = ConnectionParams(**body)
    connections.set_connection(env=env, conn_id=conn_id, conn_params=conn_params)
