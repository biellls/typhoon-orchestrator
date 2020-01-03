import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import yaml
from termcolor import colored

from typhoon.core.config import CLIConfig
from typhoon.core.dags import DagDeployment
from typhoon.core.glue import get_dag_filenames, get_dags_contents
from typhoon.core.metadata_store_interface import MetadataObjectNotFound
from typhoon.core.settings import Settings
from typhoon.remotes import Remotes


def dags_with_changes() -> List[str]:
    result = []
    for dag_file in get_dag_filenames():
        yaml_path = Path(Settings.dags_directory) / dag_file
        yaml_modified_ts = datetime.fromtimestamp(yaml_path.stat().st_mtime)
        dag_name = yaml.safe_load(yaml_path.read_text())['name']
        transpiled_path = Path(Settings.out_directory) / dag_name / f'{dag_name}.py'
        if not transpiled_path.exists():
            continue
        transpiled_created_ts = datetime.fromtimestamp(transpiled_path.stat().st_ctime)
        if yaml_modified_ts > transpiled_created_ts:
            result.append(dag_name)

    return result


def dags_without_deploy(env) -> List[str]:
    undeployed_dags = []
    config = CLIConfig(env)
    for dag_code in get_dags_contents(Settings.dags_directory):
        loaded_dag = yaml.safe_load(dag_code)
        dag_deployment = DagDeployment(dag_name=loaded_dag['name'], deployment_date=datetime.utcnow(), dag_code=dag_code)
        if loaded_dag.get('active', True):
            try:
                _ = config.metadata_store.get_dag_deployment(dag_deployment.deployment_hash)
            except MetadataObjectNotFound:
                undeployed_dags.append(dag_deployment.dag_name)
    return undeployed_dags


def get_undefined_connections_in_metadata_db(remote: Optional[str], conn_ids: List[str]):
    undefined_connections = []
    for conn_id in conn_ids:
        try:
            Settings.metadata_store(Remotes.aws_profile(remote)).get_connection(conn_id)
        except MetadataObjectNotFound:
            undefined_connections.append(conn_id)
    return undefined_connections


def get_undefined_variables_in_metadata_db(remote: Optional[str], var_ids: List[str]):
    undefined_variables = []
    for var_id in var_ids:
        try:
            Settings.metadata_store(Remotes.aws_profile(remote)).get_variable(var_id)
        except MetadataObjectNotFound:
            undefined_variables.append(var_id)
    return undefined_variables


def check_connections_yaml(remote: Optional[str]):
    if not Path('connections.yml').exists():
        print(colored('• Connections YAML not found. For better version control create', 'red'), colored('connections.yml', 'grey'))
        print(colored('  Skipping connections YAML checks...', 'red'))
        return
    conn_yml = yaml.safe_load(Path('connections.yml').read_text())
    undefined_connections = get_undefined_connections_in_metadata_db(remote, conn_ids=conn_yml.keys())
    if undefined_connections:
        print(colored('• Found connections in YAML that are not defined in the metadata database', 'yellow'))
        for conn_id in undefined_connections:
            print(
                colored('   - Connection', 'yellow'),
                colored(conn_id, 'blue'),
                colored('is not set. Try', 'yellow'),
                colored(f'typhoon set-connection {conn_id} CONN_ENV {remote}', 'grey')
            )
    else:
        print(colored('• All connections in YAML are defined in the database', 'green'))


def check_connections_dags(remote: Optional[str]):
    all_conn_ids = set()
    for dag_file in Path(Settings.dags_directory).rglob('*.yml'):
        conn_ids = re.findall(r'\$HOOK\.(\w+)', dag_file.read_text())
        all_conn_ids = all_conn_ids.union(conn_ids)
    undefined_connections = get_undefined_connections_in_metadata_db(remote, conn_ids=all_conn_ids)
    if undefined_connections:
        print(colored('• Found connections in DAGs that are not defined in the metadata database', 'yellow'))
        for conn_id in undefined_connections:
            print(
                colored('   - Connection', 'yellow'),
                colored(conn_id, 'blue'),
                colored('is not set. Try', 'yellow'),
                colored(f'typhoon set-connection {conn_id} CONN_ENV {remote}', 'grey')
            )
    else:
        print(colored('• All connections in the DAGs are defined in the database', 'green'))


def check_variables_dags(remote: Optional[str]):
    all_var_ids = set()
    for dag_file in Path(Settings.dags_directory).rglob('*.yml'):
        var_ids = re.findall(r'\$VARIABLE\.(\w+)', dag_file.read_text())
        all_var_ids = all_var_ids.union(var_ids)
    undefined_variables = get_undefined_variables_in_metadata_db(remote, var_ids=all_var_ids)
    if undefined_variables:
        print(colored('• Found variables in DAGs that are not defined in the metadata database', 'yellow'))
        for var_id in undefined_variables:
            print(
                colored('   - Variable', 'yellow'),
                colored(var_id, 'blue'),
                colored('is not set. Try', 'yellow'),
                colored(f'typhoon set-variable {var_id} VAR_TYPE VALUE {remote}', 'grey')
            )
    else:
        print(colored('• All variables in the DAGs are defined in the database', 'green'))