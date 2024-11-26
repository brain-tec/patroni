import time

from behave import step, then

@step('I start {name:name} in a cluster {cluster_name:w} as a long-running clone of {name2:name}')
def start_cluster_clone(context, name, cluster_name, name2):
    context.pctl.clone(name2, cluster_name, name, True)

@step('I start {name:name} in cluster {cluster_name:w} using long-running backup_restore')
def start_patroni(context, name, cluster_name):
    return context.pctl.start(name, custom_config={
        "scope": cluster_name,
        "postgresql": {
            'create_replica_methods': ['backup_restore'],
            "backup_restore": context.pctl.backup_restore_config(long_running=True),
            'authentication': {
                'superuser': {'password': 'patroni1'},
                'replication': {'password': 'rep-pass1'}
            }
        }
    }, max_wait_limit=-1)

@then('{name:name} is annotated with "{annotation:w}"')
def pod_annotated(context, name, annotation):
    print(context.dcs_ctl.pod_annotations(name))
    assert annotation in context.dcs_ctl.pod_annotations(name), f'pod {name} is not annotated with {annotation}'

@then('{name:name} is not annotated with "{annotation:w}"')
def pod_annotated(context, name, annotation):
    print(context.dcs_ctl.pod_annotations(name))
    assert annotation not in context.dcs_ctl.pod_annotations(name), f'pod {name} is still annotated with {annotation}'
