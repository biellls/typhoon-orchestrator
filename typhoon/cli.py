import os
import subprocess
from pathlib import Path
from shutil import copy

import click

from typhoon import connections
from typhoon.deployment.deploy import deploy_dag_requirements, copy_local_typhoon, copy_user_defined_code
from typhoon.settings import out_directory


@click.group()
def cli():
    """Typhoon CLI"""
    pass


@cli.command()
@click.argument('target_env')
def migrate(target_env):
    """Add the necessary IAM roles and DynamoDB tables"""
    from typhoon.deployment.iam import deploy_role
    deploy_role(use_cli_config=True, target_env=target_env)

    from typhoon.deployment.dynamo import create_connections_table, create_variables_table
    create_connections_table(use_cli_config=True, target_env=target_env)
    create_variables_table(use_cli_config=True, target_env=target_env)


@cli.command()
@click.argument('target_env')
def clean(target_env):
    from typhoon.deployment.iam import clean_role
    clean_role(use_cli_config=True, target_env=target_env)


@cli.command()
@click.argument('target_env')
@click.option('--debug', default=False, is_flag=True)
def build_dags(target_env, debug):
    """Build code for dags in $TYPHOON_HOME/out/"""
    _build_dags(target_env, debug)


def _build_dags(target_env, debug):
    from typhoon.deployment.deploy import clean_out, build_dag_code
    from typhoon.deployment.dags import load_dags
    from typhoon.deployment.sam import deploy_sam_template

    clean_out()

    from typhoon.core import get_typhoon_config
    config = get_typhoon_config(use_cli_config=True, target_env=target_env)

    dags = load_dags()
    deploy_sam_template(dags, use_cli_config=True, target_env=target_env)
    for dag in dags:
        build_dag_code(dag, target_env, debug)
        deploy_dag_requirements(dag, config.typhoon_version_is_local(), config.typhoon_version)
        if config.typhoon_version_is_local():
            copy_local_typhoon(dag, config.typhoon_version)

        copy(Path(__file__).parent / 'handler.py', Path(out_directory()) / dag['name'])

        copy_user_defined_code(dag)


class SubprocessError(Exception):
    pass


def run_in_subprocess(command: str):
    print(f'Executing command in shell:  {command}')
    args = command.split(' ')
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=os.environ.copy())
    stdout, stderr = p.communicate()
    stdout = stdout.decode()
    if stderr:
        raise SubprocessError(f'Error executing in console: {stderr}')
    elif 'Unable to upload artifact' in stdout:
        print(stdout)
        raise SubprocessError(f'Error executing in console: {stdout}')
    print(stdout)


@cli.command()
@click.argument('target_env')
@click.option('--build-dependencies', default=False, is_flag=True)
def deploy_dags(target_env, build_dependencies):
    from typhoon.core import get_typhoon_config

    config = get_typhoon_config(use_cli_config=True, target_env=target_env)

    template_path = Path(out_directory()) / 'template.yml'
    out_template_path = Path(out_directory()) / 'out_template.yml'
    _build_dags(target_env=target_env, debug=False)
    run_in_subprocess(
        f'sam package --template-file {template_path} --s3-bucket {config.s3_bucket} --profile {config.aws_profile}'
        f' --output-template-file {out_template_path}'
    )
    if build_dependencies:
        run_in_subprocess(f'sam build --base-dir {Path(out_directory())} '
                          f'--template {Path(out_directory()) / "template.yml"} --use-container')
    run_in_subprocess(
        f'sam deploy --template-file {out_template_path} '
        f'--stack-name {config.project_name.replace("_", "-")} --profile {config.aws_profile} '
        f'--region {config.deploy_region} --capabilities CAPABILITY_IAM'
    )


@cli.command()
@click.argument('conn_id')
@click.argument('conn_env')
@click.argument('target_env')
def set_connection(conn_id, conn_env, target_env):
    conn_params = connections.get_connection_local(conn_id, conn_env)
    connections.set_connection(
        conn_id=conn_id,
        conn_params=conn_params,
        use_cli_config=True,
        target_env=target_env,
    )


if __name__ == '__main__':
    cli()
