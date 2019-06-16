import click

from typhoon import connections
from typhoon.deployment.deploy import deploy_dag_requirements, copy_local_typhoon, copy_user_defined_code


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
def build_dags(target_env):
    """Build code for dags in $TYPHOON_HOME/out/"""
    from typhoon.deployment.deploy import clean_out, build_dag_code
    from typhoon.deployment.dags import load_dags
    from typhoon.deployment.sam import deploy_sam_template

    clean_out()

    from typhoon.core import get_typhoon_config
    config = get_typhoon_config(use_cli_config=True, target_env=target_env)

    dags = load_dags()
    deploy_sam_template(dags, use_cli_config=True, target_env=target_env)
    for dag in dags:
        build_dag_code(dag, target_env)
        deploy_dag_requirements(dag, config.typhoon_version_is_local(), config.typhoon_version)
        if config.typhoon_version_is_local():
            copy_local_typhoon(dag, config.typhoon_version)

        copy_user_defined_code(dag)


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
