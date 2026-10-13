"""Contract for Element-initiated requests to the ERP extension."""
import copy


def ref(name):
    return {'$ref': '#/components/schemas/' + name}


def pull_schemas(schemas):
    context = copy.deepcopy(schemas['Context'])
    del context['properties']['bindingId']
    context['required'].remove('bindingId')
    context['properties']['phaseState'] = {'type': 'string', 'enum': ['open', 'freezing', 'waiting_erp', 'closed']}
    context['required'].append('phaseState')
    common = {'contractVersion': {'type': 'string', 'const': '1.0'},
              'context': ref('ERPContext'),
              'organization': {'type': 'string', 'minLength': 1, 'maxLength': 200},
              'mappings': {'type': 'array', 'minItems': 15, 'maxItems': 15, 'items': ref('ERPMapping')},
              'orders': {'type': 'array', 'items': ref('ERPOrder')},
              'playerFinished': {'type': 'boolean'},
              'allocationReady': {'type': 'boolean'}}
    response = {
        'type': 'object', 'additionalProperties': False,
        'properties': {
            'contractVersion': common['contractVersion'],
            'commandId': ref('UUID'),
            'snapshot': ref('SnapshotRequest'),
            'readiness': ref('ReadinessRequest'),
            'receipt': ref('CommandReceiptRequest'),
            'salesEvents': {'type': 'array', 'maxItems': 100, 'items': ref('SalesEventRequest')},
            'executionEvents': {'type': 'array', 'maxItems': 100, 'items': ref('ExecutionEventRequest')},
        },
        'required': ['contractVersion', 'salesEvents', 'executionEvents'],
        'allOf': [
            {'if': {'required': ['receipt']}, 'then': {'required': ['commandId']}},
            {'if': {'required': ['commandId']}, 'then': {'required': ['receipt']}},
            {'if': {'required': ['readiness']}, 'then': {'required': ['snapshot']}},
        ],
    }
    return {
        'ERPOrder': {'type': 'object', 'additionalProperties': False,
                     'properties': {'command': ref('Command'),
                                    'erpDocumentIds': {'type': 'array', 'items': ref('UUID')}},
                     'required': ['command', 'erpDocumentIds']},
        'ERPMapping': {'type': 'object', 'additionalProperties': False,
                       'properties': {'itemCode': ref('Code'), 'refKey': ref('UUID'),
                                      'characteristicId': {'anyOf': [{'type': 'string', 'const': ''}, ref('UUID'),
                                                                   {'type': 'string', 'const': '00000000-0000-0000-0000-000000000000'}]},
                                      'unitCode': ref('Code'), 'coefficient': ref('PositiveQuantity')},
                       'required': ['itemCode', 'refKey', 'characteristicId', 'unitCode', 'coefficient']},
        'ERPContext': context,
        'ERPStateRequest': {'type': 'object', 'additionalProperties': False,
                            'properties': common, 'required': list(common)},
        'ERPCommandRequest': {'type': 'object', 'additionalProperties': False,
                              'properties': {**common, 'command': ref('Command')},
                              'required': [*common, 'command']},
        'ERPResponse': response,
        'ERPAcknowledgement': {'type': 'object', 'additionalProperties': False,
                               'properties': {'contractVersion': common['contractVersion'],
                                              'gameId': ref('UUID'), 'participantId': ref('UUID'),
                                              'messageIds': {'type': 'array', 'maxItems': 204, 'items': ref('UUID'), 'uniqueItems': True}},
                               'required': ['contractVersion', 'gameId', 'participantId', 'messageIds']},
    }


def pull_openapi(schemas):
    paths = {}
    for name, request in [('state', 'ERPStateRequest'), ('commands', 'ERPCommandRequest'), ('ack', 'ERPAcknowledgement')]:
        paths['/' + name] = {'post': {
            'operationId': {'state': 'readERPState', 'commands': 'executeERPCommand', 'ack': 'acknowledgeERPEvents'}[name],
            'parameters': [
                {'name': 'X-Game-Contract-Version', 'in': 'header', 'required': True,
                 'schema': {'type': 'string', 'const': '1.0'}},
                {'name': 'Idempotency-Key', 'in': 'header', 'required': True, 'schema': ref('UUID')},
            ],
            'requestBody': {'required': True, 'content': {'application/json': {'schema': ref(request)}}},
            'responses': {status: {'description': description,
                                  'content': {'application/json': {'schema': ref('ERPResponse')}}}
                          for status, description in [('200', 'Данные или выполненная команда'),
                                                       ('202', 'Команда выполняется; повторить тот же commandId')]},
        }}
        if name == 'ack':
            paths['/ack']['post']['responses'] = {'204': {'description': 'Сообщения приняты Элементом'}}
    return {
        'openapi': '3.1.0',
        'info': {'title': 'ERP API called by Element', 'version': '1.0',
                 'description': 'Элемент инициирует все запросы. Аутентификация пользователем ERP; '
                                'bindingId и пользователь приложения Элемента не требуются. '
                                'snapshotId в receipt/readiness равен messageId снимка из этого ответа. '
                                'POST /state только читает данные. Максимум ответа 1 МиБ.'},
        'servers': [{'url': '/hs/game/v1'}],
        'security': [{'erpBasic': []}], 'paths': paths,
        'components': {'securitySchemes': {'erpBasic': {'type': 'http', 'scheme': 'basic'}},
                       'schemas': {k: v for k, v in schemas.items() if k != 'Context'}},
    }
