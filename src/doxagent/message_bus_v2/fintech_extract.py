"""Read public structured data delivered by publisher frontends; no JS execution."""
import json
import re

from lxml import html


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def nuxt_data(doc):
    nodes = doc.xpath('//script[@id="__NUXT_DATA__"]')
    if not nodes:
        raise ValueError('publisher_nuxt_data_missing')
    values = json.loads(nodes[0].text)
    memo = {}

    def resolve(index):
        if not isinstance(index, int) or isinstance(index, bool):
            return index
        if index < 0:
            return None
        if index in memo:
            return memo[index]
        value = values[index]
        memo[index] = None
        if isinstance(value, dict):
            result = {k: resolve(v) for k, v in value.items()}
        elif isinstance(value, list):
            if value and isinstance(value[0], str) and value[0] in {
                'Reactive', 'ShallowReactive', 'Ref', 'ShallowRef', 'EmptyRef', 'EmptyShallowRef',
            }:
                result = resolve(value[1])
            elif value and value[0] == 'Date':
                result = value[1]
            else:
                result = [resolve(v) for v in value]
        else:
            result = value
        memo[index] = result
        return result

    return resolve(0)


def rsc_data(doc):
    fragments = []
    for node in doc.xpath('//script[not(@src)]'):
        match = re.search(r'self\.__next_f\.push\((.*)\)\s*;?\s*$', node.text or '', re.S)
        if match:
            value = json.loads(match[1])
            if len(value) > 1 and value[0] == 1 and isinstance(value[1], str):
                fragments.append(value[1])
    result = []
    for line in ''.join(fragments).splitlines():
        _, separator, payload = line.partition(':')
        if separator and payload.startswith(('[', '{')):
            try:
                result.append(json.loads(payload))
            except ValueError:
                continue  # RSC also carries module imports and length-prefixed text.
    return result


def document(raw):
    return html.fromstring(raw)
