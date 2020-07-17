import decimal
from enum import Enum
from typing import Optional, Union

from boto3.dynamodb.types import TypeSerializer, TypeDeserializer

from typhoon.aws.exceptions import TyphoonResourceNotFoundError
from typhoon.aws.plumbing.boto3_plumbing import boto3_session

"""Module containing low-level functions to interact with DynamoDB
In general all functions take a dynamodb client or resource.
We do not worry about creating those resources/clients in this layer.
"""


class DynamoDBConnectionType(Enum):
    RESOURCE = 'resource'
    CLIENT = 'client'


def dynamodb_connection(
        aws_profile: Optional[str] = None,
        conn_type: Union[str, DynamoDBConnectionType] = 'resource',
        aws_region: Optional[str] = None,
        endpoint_url: Optional[str] = None,
):
    session = boto3_session(aws_profile)
    aws_region = aws_region or getattr(session, 'region_name', None)
    extra_params = {'region_name': aws_region} if aws_region else {}
    if endpoint_url:
        extra_params = {
            'aws_access_key_id': 'dummy',
            'aws_secret_access_key': 'dummy',
            'endpoint_url': endpoint_url,
            **extra_params,
        }

    if conn_type is DynamoDBConnectionType.CLIENT or conn_type == 'client':
        ddb = session.client('dynamodb', **extra_params)
    elif conn_type is DynamoDBConnectionType.RESOURCE or conn_type == 'resource':
        ddb = session.resource('dynamodb', **extra_params)
    else:
        raise ValueError(f'Expected conn_type as client or resource, found: {conn_type}')

    return ddb


def scan_dynamodb_table(ddb_resource, table_name: str):
    table = ddb_resource.Table(table_name)
    response = table.scan()
    data = response['Items']

    while 'LastEvaluatedKey' in response:
        response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
        data.extend(response['Items'])
    return data


def dynamodb_table_exists(ddb_client, table_name: str):
    existing_tables = ddb_client.list_tables()['TableNames']
    return table_name in existing_tables


def create_dynamodb_table(
        ddb_client,
        table_name: str,
        primary_key: str,
        range_key: Union[str, None] = None,  # May have other types in the future
        read_capacity_units: int = 1,
        write_capacity_units: int = 1,
):
    key_schema = [
        {
            'AttributeName': primary_key,
            'KeyType': 'HASH'
        },
    ]
    attribute_definitions = [
        {
            'AttributeName': primary_key,
            'AttributeType': 'S'
        },
    ]

    if range_key:
        key_schema.append({
            {
                'AttributeName': range_key,
                'KeyType': 'RANGE'
            },
        })
        if isinstance(range_key, str):
            attribute_type = 'S'
        else:
            raise ValueError(f'Expected range key to be in [str]. Found: {type(range_key)}')
        attribute_definitions.append({
            {
                'AttributeName': attribute_type,
                'AttributeType': 'S'
            },
        })

    table = ddb_client.create_table(
        TableName=table_name,
        KeySchema=key_schema,
        AttributeDefinitions=attribute_definitions,
        ProvisionedThroughput={
            'ReadCapacityUnits': read_capacity_units,
            'WriteCapacityUnits': write_capacity_units
        }
    )
    return table


def dynamodb_put_item(ddb_client, table_name: str, item: dict):
    serializer = TypeSerializer()
    serialized_item = serializer.serialize(item)['M']
    ddb_client.put_item(
        TableName=table_name,
        Item=serialized_item)


def dynamodb_get_item(ddb_client, table_name: str, key_name: str, key_value: str):
    response = ddb_client.get_item(
        TableName=table_name,
        Key={key_name: {'S': key_value}}
    )
    if 'Item' not in response:
        raise TyphoonResourceNotFoundError(
            f'Item {key_name}="{key_value}" does not exist in DynamoDB table {table_name}')
    deserializer = TypeDeserializer()
    return {k: deserializer.deserialize(v) for k, v in response['Item'].items()}


def dynamodb_delete_item(ddb_client, table_name, key_name: str, key_value: str):
    ddb_client.delete_item(
        TableName=table_name,
        Key={key_name: {'S': key_value}}
    )


def replace_decimals(obj):
    if isinstance(obj, list):
        for i in range(len(obj)):
            obj[i] = replace_decimals(obj[i])
        return obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            obj[k] = replace_decimals(v)
        return obj
    elif isinstance(obj, set):
        return set(replace_decimals(i) for i in obj)
    elif isinstance(obj, decimal.Decimal):
        if obj % 1 == 0:
            return int(obj)
        else:
            return float(obj)
    else:
        return obj