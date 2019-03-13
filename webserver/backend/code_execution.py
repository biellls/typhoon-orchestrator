import re
from typing import List, Any


def _replace_special_vars(string: str):
    string = re.sub(r'\$(\d+)', r'results[\1 - 1]', string)
    string = re.sub(r'\$SOURCE', 'source_data', string)
    string = re.sub(r'\$DAG_CONFIG\.([\w]+)', r'dag_config["""\1"""]', string)
    string = string.replace('$DAG_CONFIG', 'dag_config')
    string = string.replace('$BATCH_NUM', '2')
    return string


# noinspection PyUnresolvedReferences
def run_transformations(source_data: Any, dag_config: dict, transformations: List[str]):
    import typhoon.contrib.transformations as typhoon
    results = []
    for transform in map(_replace_special_vars, transformations):
        try:
            result = eval(transform, globals(), locals())
        except Exception as e:
            result = {'__error__': f'{type(e).__name__}: {str(e)}'}
        results.append(result)

    return results[-1]
