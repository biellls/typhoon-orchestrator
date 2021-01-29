import os
import pydoc
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import click
import pkg_resources
import pygments
import yaml
from dataclasses import asdict
from pygments.formatters.terminal256 import Terminal256Formatter
from pygments.lexers.data import YamlLexer
from pygments.lexers.python import PythonLexer
from tabulate import tabulate
from termcolor import colored

from typhoon import local_config, connections
from typhoon.cli_helpers.cli_completion import get_remote_names, get_dag_names, get_conn_envs, get_conn_ids, \
    get_var_types, get_node_names, get_edge_names, get_deploy_targets
from typhoon.cli_helpers.status import dags_with_changes, dags_without_deploy, check_connections_yaml, \
    check_connections_dags, check_variables_dags
from typhoon.connections import Connection
from typhoon.core import DagContext
from typhoon.core.dags import DAG
from typhoon.core.glue import get_dag_errors, load_dag
from typhoon.core.settings import Settings, EnvVarName, set_settings_from_file
from typhoon.deployment.packaging import build_all_dags
from typhoon.deployment.targets.airflow.airflow_build import build_all_dags_airflow
from typhoon.handler import run_dag
from typhoon.introspection.introspect_transformations import run_transformations, TransformationResult
from typhoon.local_config import EXAMPLE_CONFIG
from typhoon.metadata_store_impl.sqlite_metadata_store import SQLiteMetadataStore
from typhoon.remotes import Remotes
from typhoon.variables import Variable, VariableType
from typhoon.watch import watch_changes

ascii_art_logo = r"""
 _________  __  __   ______   ___   ___   ______   ______   ___   __        
/________/\/_/\/_/\ /_____/\ /__/\ /__/\ /_____/\ /_____/\ /__/\ /__/\      
\__.::.__\/\ \ \ \ \\:::_ \ \\::\ \\  \ \\:::_ \ \\:::_ \ \\::\_\\  \ \     
   \::\ \   \:\_\ \ \\:(_) \ \\::\/_\ .\ \\:\ \ \ \\:\ \ \ \\:. `-\  \ \    
    \::\ \   \::::_\/ \: ___\/ \:: ___::\ \\:\ \ \ \\:\ \ \ \\:. _    \ \   
     \::\ \    \::\ \  \ \ \    \: \ \\::\ \\:\_\ \ \\:\_\ \ \\. \`-\  \ \  
      \__\/     \__\/   \_\/     \__\/ \::\/ \_____\/ \_____\/ \__\/ \__\/  
"""


def set_settings_from_remote(remote: str):
    if remote:
        if remote not in Remotes.remotes_config.keys():
            print(f'Remote {remote} is not defined in .typhoonremotes. Found : {list(Remotes.remotes_config.keys())}',
                  file=sys.stderr)
            sys.exit(-1)
        Settings.metadata_db_url = Remotes.metadata_db_url(remote)
        if Remotes.use_name_as_suffix(remote):
            Settings.metadata_suffix = remote


@click.group()
def cli():
    """Typhoon CLI"""
    if Settings.typhoon_home:
        print(f'${EnvVarName.PROJECT_HOME} defined from env variable to "{Settings.typhoon_home}"')
        return

    typhoon_config_file = local_config.find_typhoon_cfg_in_cwd_or_parents()
    if not typhoon_config_file:
        print('Did not find typhoon.cfg in current directory or any of its parent directories')
        return
    os.environ[EnvVarName.PROJECT_HOME] = str(typhoon_config_file.parent)
    set_settings_from_file(typhoon_config_file)
    if not Settings.project_name:
        print(f'Project name not set in "{Settings.typhoon_home}/typhoon.cfg "')


@cli.command()
@click.argument('project_name')
@click.option('--deploy-target', autocompletion=get_deploy_targets, required=False, default='typhoon')
def init(project_name: str, deploy_target: str):
    """Create a new Typhoon project"""
    example_project_path = Path(pkg_resources.resource_filename('typhoon', 'examples')) / 'hello_world'
    if deploy_target == 'airflow':
        dest = Path.cwd() / 'typhoon_extension'
    else:
        dest = Path.cwd() / project_name
    shutil.copytree(str(example_project_path), str(dest))
    (dest / 'typhoon.cfg').write_text(EXAMPLE_CONFIG.format(project_name=project_name, deploy_target=deploy_target))
    (dest / 'dag_schema.json').write_text(DAG.schema_json(indent=2))
    print(f'Project created in {dest}')


@cli.command()
@click.argument('remote', autocompletion=get_remote_names, required=False, default=None)
def status(remote: Optional[str]):
    """Information on project status"""
    set_settings_from_remote(remote)

    print(colored(ascii_art_logo, 'cyan'))
    if not Settings.typhoon_home:
        print(colored(f'FATAL: typhoon home not found...', 'red'))
        return
    else:
        print(colored('• Typhoon home defined as', 'green'), colored(Settings.typhoon_home, 'grey'))

    metadata_store = Settings.metadata_store(Remotes.aws_profile(remote))
    if metadata_store.exists():
        print(colored('• Metadata database found in', 'green'), colored(Settings.metadata_db_url, 'grey'))
        check_connections_yaml(remote)
        check_connections_dags(remote)
        check_variables_dags(remote)
    elif isinstance(metadata_store, SQLiteMetadataStore):
        print(colored('• Metadata store not found for', 'yellow'), colored(Settings.metadata_db_url, 'grey'))
        print(
            colored('   - It will be created upon use, or create by running (idempotent) command', color='blue'),
            colored(f'typhoon migrate{" " + remote if remote else ""}', 'grey'))
        print(colored('  Skipping connections and variables checks...', 'red'))
    else:
        print(colored('• Metadata store not found or incomplete for', 'red'), colored(Settings.metadata_db_url, 'grey'))
        print(
            colored('   - Fix by running (idempotent) command', color='blue'),
            colored(f'typhoon metadata migrate{" " + remote if remote else ""}', 'grey'))
        print(colored('  Skipping connections and variables checks...', 'red'))

    if not remote:
        changed_dags = dags_with_changes()
        if changed_dags:
            print(colored('• Unbuilt changes in DAGs...', 'yellow'), colored('To rebuild run', 'white'),
                  colored(f'typhoon dag build{" " + remote if remote else ""} --all [--debug]', 'grey'))
            for dag in changed_dags:
                print(colored(f'   - {dag}', 'blue'))
        else:
            print(colored('• DAGs up to date', 'green'))
    else:
        undeployed_dags = dags_without_deploy(remote)
        if undeployed_dags:
            print(colored('• Undeployed changes in DAGs...', 'yellow'), colored('To deploy run', 'white'),
                  colored(f'typhoon dag push {remote} --all [--build-dependencies]', 'grey'))
            for dag in undeployed_dags:
                print(colored(f'   - {dag}', 'blue'))
        else:
            print(colored('• DAGs up to date', 'green'))


@cli.group(name='remote')
def cli_remote():
    """Manage Typhoon remotes"""
    pass


@cli_remote.command(name='add')
@click.argument('remote')       # No autocomplete because the remote is new
@click.option('--aws-profile')
@click.option('--metadata-db-url')
@click.option('--use-name-as-suffix', is_flag=True, default=False)
def remote_add(remote: str, aws_profile: str, metadata_db_url: str, use_name_as_suffix: bool):
    """Add a remote for deployments and management"""
    Remotes.add_remote(remote, aws_profile, metadata_db_url, use_name_as_suffix)
    print(f'Added remote {remote}')


@cli_remote.command(name='ls')
@click.option('-l', '--long', is_flag=True, default=False)
def remote_list(long: bool):
    """List configured Typhoon remotes"""
    if long:
        header = ['REMOTE_NAME', 'AWS_PROFILE', 'USE_NAME_AS_SUFFIX', 'METADATA_DB_URL']
        table_body = [
            [remote, Remotes.aws_profile(remote), Remotes.use_name_as_suffix(remote), Remotes.metadata_db_url(remote)]
            for remote in Remotes.remote_names
        ]
        print(tabulate(table_body, header, 'plain'))
    else:
        for remote in Remotes.remote_names:
            print(remote)


@cli_remote.command(name='rm')
@click.argument('remote', autocompletion=get_remote_names)       # No autocomplete because the remote is new
def remote_add(remote: str):
    """Remove remote"""
    Remotes.remove_remote(remote)
    print(f'Removed remote {remote}')


@cli.group(name='metadata')
def cli_metadata():
    """Manage Typhoon metadata"""
    pass


@cli_metadata.command()
@click.argument('remote', autocompletion=get_remote_names)
def migrate(remote: str):
    """Create the necessary metadata tables"""
    set_settings_from_remote(remote)
    print(f'Migrating {Settings.metadata_db_url}...')
    Settings.metadata_store(aws_profile=Remotes.aws_profile(remote)).migrate()


@cli_metadata.command(name='info')
@click.argument('remote', autocompletion=get_remote_names, required=False, default=None)
def metadata_info(remote: Optional[str]):
    """Info on metadata connection and table names"""
    set_settings_from_remote(remote)
    print(ascii_art_logo)
    print(f'Metadata database URL:\t{Settings.metadata_db_url}')
    print(f'Connections table name:\t{Settings.connections_table_name}')
    print(f'Variables table name:\t{Settings.variables_table_name}')
    print(f'DAG deployments table name:\t{Settings.dag_deployments_table_name}')


@cli.group(name='dag')
def cli_dags():
    """Manage Typhoon DAGs"""
    pass


@cli_dags.command(name='ls')
@click.argument('remote', autocompletion=get_remote_names, required=False, default=None)
@click.option('-l', '--long', is_flag=True, default=False)
def list_dags(remote: Optional[str], long: bool):
    set_settings_from_remote(remote)
    metadata_store = Settings.metadata_store(Remotes.aws_profile(remote))
    if long:
        header = ['DAG_NAME', 'DEPLOYMENT_DATE']
        table_body = [
            [x.dag_name, x.deployment_date.isoformat()]
            for x in metadata_store.get_dag_deployments()
        ]
        print(tabulate(table_body, header, 'plain'))
        if not remote:
            dag_errors = get_dag_errors()
            if dag_errors:
                header = ['DAG_NAME', 'ERROR LOCATION', 'ERROR MESSAGE']
                table_body = [
                    [dag_name, error[0]['loc'], error[0]['msg']]
                    for dag_name, error in dag_errors.items()
                ]
                print(colored(tabulate(table_body, header, 'plain'), 'red'), file=sys.stderr)
    else:
        for dag_deployment in metadata_store.get_dag_deployments():
            print(dag_deployment.dag_name)
        if not remote:
            for dag_name, _ in get_dag_errors().items():
                print(colored(dag_name, 'red'), file=sys.stderr)


@cli_dags.command(name='build')
@click.argument('dag_name', autocompletion=get_dag_names, required=False, default=None)
@click.option('--all', 'all_', is_flag=True, default=False, help='Build all DAGs (mutually exclusive with DAG_NAME)')
def build_dags(dag_name: Optional[str], all_: bool):
    """Build code for dags in $TYPHOON_HOME/out/"""
    if dag_name and all_:
        raise click.UsageError(f'Illegal usage: DAG_NAME is mutually exclusive with --all')
    elif dag_name is None and not all_:
        raise click.UsageError(f'Illegal usage: Need either DAG_NAME or --all')
    if all_:
        dag_errors = get_dag_errors()
        if dag_errors:
            print(f'Found errors in the following DAGs:')
            for dag_name in dag_errors.keys():
                print(f'  - {dag_name}\trun typhoon dag build {dag_name}')

        if Settings.deploy_target == 'typhoon':
            build_all_dags(remote=None)
        else:
            build_all_dags_airflow(remote=None)
    else:
        dag_errors = get_dag_errors().get(dag_name)
        if dag_errors:
            print(f'FATAL: DAG {dag_name} has errors', file=sys.stderr)
            header = ['ERROR_LOCATION', 'ERROR_MESSAGE']
            table_body = [
                [error['loc'], error['msg']]
                for error in dag_errors
            ]
            print(tabulate(table_body, header, 'plain'), file=sys.stderr)
            sys.exit(-1)
        if Settings.deploy_target == 'typhoon':
            build_all_dags(remote=None, matching=dag_name)
        else:
            build_all_dags_airflow(remote=None, matching=dag_name)


@cli_dags.command(name='watch')
@click.argument('dag_name', autocompletion=get_dag_names, required=False, default=None)
@click.option('--all', 'all_', is_flag=True, default=False, help='Build all DAGs (mutually exclusive with DAG_NAME)')
def watch_dags(dag_name: Optional[str], all_: bool):
    """Watch DAGs and build code for dags in $TYPHOON_HOME/out/"""
    if dag_name and all_:
        raise click.UsageError(f'Illegal usage: DAG_NAME is mutually exclusive with --all')
    elif dag_name is None and not all_:
        raise click.UsageError(f'Illegal usage: Need either DAG_NAME or --all')
    if all_:
        print('Watching all DAGs for changes...')
        watch_changes()
    else:
        print(f'Watching DAG {dag_name} for changes...')
        watch_changes(patterns=f'{dag_name}.yml')


def run_local_dag(dag_name: str, execution_date: datetime):
    dag_path = Settings.out_directory / dag_name / f'{dag_name}.py'
    if not dag_path.exists():
        print(f"Error: {dag_path} doesn't exist. Build DAGs")
    try:
        run_dag(dag_name, str(execution_date), capture_logs=False)
    except FileNotFoundError:
        print(f'DAG {dag_name} could not be built')


@cli_dags.command(name='run')
@click.argument('remote', autocompletion=get_remote_names, required=False, default=None)
@click.option('--dag-name', autocompletion=get_dag_names)
@click.option('--execution-date', default=None, is_flag=True, type=click.DateTime(), help='DAG execution date as YYYY-mm-dd')
def cli_run_dag(remote: Optional[str], dag_name: str, execution_date: Optional[datetime]):
    """Run a DAG for a specific date. Will create a metadata entry in the database (TODO: create entry)."""
    set_settings_from_remote(remote)
    if execution_date is None:
        execution_date = datetime.now()
    if remote is None:
        print(f'Running {dag_name} from local build...')
        dag_errors = get_dag_errors().get(dag_name)
        if dag_errors:
            print(f'FATAL: DAG {dag_name} has errors', file=sys.stderr)
            header = ['ERROR_LOCATION', 'ERROR_MESSAGE']
            table_body = [
                [error['loc'], error['msg']]
                for error in dag_errors
            ]
            print(tabulate(table_body, header, 'plain'), file=sys.stderr)
            sys.exit(-1)

        build_all_dags(remote=None, matching=dag_name)
        # Sets the env variable for metadata store to the sqlite in CWD if not set, because the CWD will be different at
        # runtime
        Settings.metadata_db_url = Settings.metadata_db_url
        run_local_dag(dag_name, execution_date)
    else:
        # TODO: Run lambda function
        pass


@cli_dags.command(name='definition')
@click.option('--dag-name', autocompletion=get_dag_names)
def dag_definition(dag_name: str):
    matching_dags = list(Settings.dags_directory.rglob(f'*{dag_name}.yml'))
    if not matching_dags:
        print(f'FATAL: No DAGs found matching {dag_name}.yml', file=sys.stderr)
        sys.exit(-1)
    elif len(matching_dags) > 1:
        print(f'FATAL: Expected one matching DAG for {dag_name}.yml. Found {len(matching_dags)}', file=sys.stderr)
    out = colored(ascii_art_logo, 'cyan') + '\n' + pygments.highlight(
        code=matching_dags[0].read_text(),
        lexer=YamlLexer(),
        formatter=Terminal256Formatter()
    )
    pydoc.pager(out)
    print(matching_dags[0])


@cli_dags.group(name='node')
def cli_nodes():
    """Manage Typhoon DAG nodes"""
    pass


def _get_dag(remote: str, dag_name: str) -> DAG:
    if remote is None:
        result = load_dag(dag_name, ignore_errors=True)
        if not result:
            print(f'FATAL: No dags found matching the name "{dag_name}"', file=sys.stderr)
            sys.exit(-1)
        dag, _ = result
    else:
        metadata_db = Settings.metadata_store(Remotes.aws_profile(remote))
        dag_deployments = metadata_db.get_dag_deployments()
        matching_dag_deployments = [x for x in dag_deployments if x.dag_name == dag_name]
        if not matching_dag_deployments:
            print(f'FATAL: No dags found matching the name "{dag_name}"', file=sys.stderr)
            sys.exit(-1)
        latest_dag_deployment = max(*matching_dag_deployments, key=lambda x: x.deployment_date)
        dag = DAG.parse_obj(yaml.safe_load(latest_dag_deployment.dag_code))
    return dag


@cli_nodes.command(name='ls')
@click.argument('remote', autocompletion=get_remote_names, required=False, default=None)
@click.option('--dag-name', autocompletion=get_dag_names)
@click.option('-l', '--long', is_flag=True, default=False)
def list_nodes(remote: Optional[str], dag_name: str, long: bool):
    """List nodes for DAG"""
    set_settings_from_remote(remote)
    dag = _get_dag(remote, dag_name)
    if long:
        header = ['NODE_NAME', 'FUNCTION', 'ASYNCHRONOUS']
        table_body = [
            [node_name, node.function, node.asynchronous]
            for node_name, node in dag.nodes.items()
        ]
        print(tabulate(table_body, header, 'plain'))
    else:
        for node_name, _ in dag.nodes.items():
            print(node_name)


@cli_nodes.command(name='definition')
@click.argument('remote', autocompletion=get_remote_names, required=False, default=None)
@click.option('--dag-name', autocompletion=get_dag_names)
@click.option('--node-name', autocompletion=get_node_names)
def node_definition(remote: Optional[str], dag_name: str, node_name: str):
    """Show node definition"""
    print(colored(ascii_art_logo, 'cyan'))
    set_settings_from_remote(remote)
    dag = _get_dag(remote, dag_name)
    if node_name not in dag.nodes.keys():
        print(f'FATAL: No nodes found matching the name "{node_name}" in dag {dag_name}', file=sys.stderr)
        sys.exit(-1)
    print(
        pygments.highlight(
            code=yaml.dump(dag.nodes[node_name].dict(), default_flow_style=False, sort_keys=False),
            lexer=YamlLexer(),
            formatter=Terminal256Formatter()
        )
    )


@cli_dags.group(name='edge')
def cli_edges():
    """Manage Typhoon DAG edges"""
    pass


@cli_edges.command(name='ls')
@click.argument('remote', autocompletion=get_remote_names, required=False, default=None)
@click.option('--dag-name', autocompletion=get_dag_names, required=True)
@click.option('-l', '--long', is_flag=True, default=False)
def list_edges(remote: Optional[str], dag_name: str, long: bool):
    """List edges for DAG"""
    set_settings_from_remote(remote)
    dag = _get_dag(remote, dag_name)
    if long:
        header = ['EDGE_NAME', 'SOURCE', 'DESTINATION']
        table_body = [
            [edge_name, edge.source, edge.destination]
            for edge_name, edge in dag.edges.items()
        ]
        print(tabulate(table_body, header, 'plain'))
    else:
        for edge_name, _ in dag.edges.items():
            print(edge_name)


@cli_edges.command(name='definition')
@click.argument('remote', autocompletion=get_remote_names, required=False, default=None)
@click.option('--dag-name', autocompletion=get_dag_names)
@click.option('--edge-name', autocompletion=get_edge_names)
def edge_definition(remote: Optional[str], dag_name: str, edge_name: str):
    """Show edge definition"""
    print(colored(ascii_art_logo, 'cyan'))
    set_settings_from_remote(remote)
    dag = _get_dag(remote, dag_name)
    if edge_name not in dag.edges.keys():
        print(f'FATAL: No edges found matching the name "{edge_name}" in dag {dag_name}', file=sys.stderr)
        sys.exit(-1)
    print(
        pygments.highlight(
            code=yaml.dump(dag.edges[edge_name].dict(), default_flow_style=False, sort_keys=False),
            lexer=YamlLexer(),
            formatter=Terminal256Formatter()
        )
    )


@cli_edges.command(name='test')
@click.argument('remote', autocompletion=get_remote_names, required=False, default=None)
@click.option('--dag-name', autocompletion=get_dag_names)
@click.option('--edge-name', autocompletion=get_edge_names)
@click.option('--input', 'input_', help='Input batch to node transformations')
@click.option('--execution-date', type=click.DateTime(), default=None, help='Input batch to node transformations')
@click.option('--eval', 'eval_', is_flag=True, default=False, help='If true evaluate the input string')
def edge_test(remote: Optional[str], dag_name: str, edge_name: str, input_, execution_date: datetime, eval_: bool):
    """Show node definition"""
    set_settings_from_remote(remote)
    dag = _get_dag(remote, dag_name)
    if edge_name not in dag.edges.keys():
        print(f'FATAL: No edges found matching the name "{edge_name}" in dag {dag_name}', file=sys.stderr)
        sys.exit(-1)
    if eval_:
        input_ = eval(input_)
    transformation_results = run_transformations(
        dag.edges[edge_name],
        input_,
        DagContext(execution_date=execution_date or datetime.now()))
    for result in transformation_results:
        if isinstance(result, TransformationResult):
            highlighted_result = pygments.highlight(
                    code=result.pretty_result,
                    lexer=PythonLexer(),
                    formatter=Terminal256Formatter()
                )
            print(colored(f'{result.config_item}:', 'green'), highlighted_result, end='')
        else:
            print(f'{result.config_item}: Error {result.error_type} {result.message}', file=sys.stderr)


@cli.group(name='connection')
def cli_connection():
    """Manage Typhoon connections"""
    pass


@cli_connection.command(name='ls')
@click.argument('remote', autocompletion=get_remote_names, required=False, default=None)
@click.option('-l', '--long', is_flag=True, default=False)
def list_connections(remote: Optional[str], long: bool):
    """List connections in the metadata store"""
    set_settings_from_remote(remote)
    metadata_store = Settings.metadata_store(Remotes.aws_profile(remote))
    if long:
        header = ['CONN_ID', 'TYPE', 'HOST', 'PORT', 'SCHEMA']
        table_body = [
            [conn.conn_id, conn.conn_type, conn.host, conn.port, conn.schema]
            for conn in metadata_store.get_connections()
        ]
        print(tabulate(table_body, header, 'plain'))
    else:
        for conn in metadata_store.get_connections():
            print(conn.conn_id)


@cli_connection.command(name='add')
@click.argument('remote', autocompletion=get_remote_names, required=False, default=None)
@click.option('--conn-id', autocompletion=get_conn_ids)
@click.option('--conn-env', autocompletion=get_conn_envs)
def add_connection(remote: Optional[str], conn_id: str, conn_env: str):
    """Add connection to the metadata store"""
    set_settings_from_remote(remote)
    metadata_store = Settings.metadata_store(Remotes.aws_profile(remote))
    conn_params = connections.get_connection_local(conn_id, conn_env)
    metadata_store.set_connection(Connection(conn_id=conn_id, **asdict(conn_params)))
    print(f'Connection {conn_id} added')


@cli_connection.command(name='rm')
@click.argument('remote', autocompletion=get_remote_names, required=False, default=None)
@click.option('--conn-id', autocompletion=get_conn_ids, required=True)
def remove_connection(remote: Optional[str], conn_id: str):
    """Remove connection from the metadata store"""
    set_settings_from_remote(remote)
    metadata_store = Settings.metadata_store(Remotes.aws_profile(remote))
    metadata_store.delete_connection(conn_id)
    print(f'Connection {conn_id} deleted')


@cli_connection.command(name='definition')
def connections_definition():
    """Connection definition in connections.yml"""
    out = pygments.highlight(
        code=(Settings.typhoon_home / 'connections.yml').read_text(),
        lexer=YamlLexer(),
        formatter=Terminal256Formatter()
    )
    pydoc.pager(out)


@cli.group(name='variable')
def cli_variable():
    """Manage Typhoon variables"""
    pass


@cli_variable.command(name='ls')
@click.argument('remote', autocompletion=get_remote_names, required=False, default=None)
@click.option('-l', '--long', is_flag=True, default=False)
def list_variables(remote: Optional[str], long: bool):
    """List variables in the metadata store"""
    def var_contents(var: Variable) -> str:
        if var.type == VariableType.NUMBER:
            return var.contents
        else:
            return f'"{var.contents}"' if len(var.contents) < max_len_var else f'"{var.contents[:max_len_var]}"...'
    set_settings_from_remote(remote)
    metadata_store = Settings.metadata_store(Remotes.aws_profile(remote))
    if long:
        max_len_var = 40
        header = ['VAR_ID', 'TYPE', 'CONTENT']
        table_body = [
            [var.id, var.type, var_contents(var)]
            for var in metadata_store.get_variables()
        ]
        print(tabulate(table_body, header, 'plain'))
    else:
        for var in metadata_store.get_variables():
            print(var.id)


@cli_variable.command(name='add')
@click.argument('remote', autocompletion=get_remote_names, required=False, default=None)
@click.option('--var-id', required=True)
@click.option('--var-type', autocompletion=get_var_types, help=f'One of {get_var_types(None, None, "")}')
@click.option('--contents', prompt=True, help='Value for the variable. Can be piped from STDIN or prompted if empty.')
def add_variable(remote: Optional[str], var_id: str, var_type: str, contents):
    """Add variable to the metadata store"""
    set_settings_from_remote(remote)
    metadata_store = Settings.metadata_store(Remotes.aws_profile(remote))
    var = Variable(var_id, VariableType[var_type.upper()], contents)
    metadata_store.set_variable(var)
    print(f'Variable {var_id} added')


@cli_variable.command(name='rm')
@click.argument('remote', autocompletion=get_remote_names, required=False, default=None)
@click.option('--var-id')
def remove_variable(remote: Optional[str], var_id: str):
    """Remove connection from the metadata store"""
    set_settings_from_remote(remote)
    metadata_store = Settings.metadata_store(Remotes.aws_profile(remote))
    metadata_store.delete_variable(var_id)
    print(f'Variable {var_id} deleted')


class SubprocessError(Exception):
    pass


def run_in_subprocess(command: str, cwd: str):
    print(f'Executing command in shell:  {command}')
    args = command.split(' ')
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=os.environ.copy(), cwd=cwd)
    stdout, stderr = p.communicate()
    stdout = stdout.decode()
    if stderr:
        raise SubprocessError(f'Error executing in console: {stderr}')
    elif 'Unable to upload artifact' in stdout:
        print(stdout)
        raise SubprocessError(f'Error executing in console: {stdout}')
    print(stdout)


@cli.command()
def webserver():
    subprocess.Popen(
        ["npm", "run", "serve"],
        cwd=str(Path(__file__).parent.parent/'webserver/typhoon_webserver/frontend'))
    sys.path.append(str(Path(__file__).parent.parent/'webserver/typhoon_webserver/backend/'))
    from core import app
    app.run()


# @cli.command()
# @click.argument('remote', autocompletion=get_remote_names)
# @click.option('--build-dependencies', default=False, is_flag=True, help='Build DAG dependencies in Docker container')
# def deploy_dags(remote, build_dependencies):
#     from typhoon.core import get_typhoon_config
#
#     config = get_typhoon_config(use_cli_config=True, target_env=target_env)
#
#     build_all_dags(target_env=target_env, debug=False)
#     if build_dependencies:
#         run_in_subprocess(f'sam build --use-container', cwd=out_directory())
#     build_dir = str(Path(out_directory()) / '.aws-sam' / 'build')
#     run_in_subprocess(
#         f'sam package --template-file template.yaml --s3-bucket {config.s3_bucket} --profile {config.aws_profile}'
#         f' --output-template-file out_template.yaml',
#         cwd=build_dir
#     )
#     run_in_subprocess(
#         f'sam deploy --template-file out_template.yaml '
#         f'--stack-name {config.project_name.replace("_", "-")} --profile {config.aws_profile} '
#         f'--region {config.deploy_region} --capabilities CAPABILITY_IAM',
#         cwd=build_dir
#     )
#
#     if not config.development_mode:
#         for dag_code in get_dags_contents(settings.dags_directory()):
#             loaded_dag = yaml.safe_load(dag_code)
#             if loaded_dag.get('active', True):
#                 dag_deployment = DagDeployment(loaded_dag['name'], deployment_date=datetime.utcnow(), dag_code=dag_code)
#                 config.metadata_store.set_dag_deployment(dag_deployment)

if __name__ == '__main__':
    cli()
