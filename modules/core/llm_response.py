"""Bounded, content-free diagnostics for the legacy local HTTP backend."""
from dataclasses import dataclass

# Keep historical ceilings until a model-specific operator trial qualifies reductions.
DEFAULT_TOKENS = {'fetch': 4096, 'translate': 4096, 'posts': 4096, 'flash': 4096,
                  'bulletin': 4096, 'report': 4096, 'editions': 4096, 'atlas': 4096}


def task_for(caller, operation=None):
    return 'translate' if operation in ('translate', 'translation', 'title_translation') else caller


def budgets(config):
    """Fail configuration closed; never log values supplied by an operator."""
    tokens = dict(DEFAULT_TOKENS)
    overrides = config.get('max_tokens_by_task', {})
    waits = config.get('lock_wait_seconds', {})
    if not isinstance(overrides, dict) or not isinstance(waits, dict):
        raise ValueError('Invalid LLM task budgets')
    for task, value in overrides.items():
        if task not in tokens or type(value) is not int or not 64 <= value <= 32768:
            raise ValueError('Invalid LLM output budget')
        tokens[task] = value
    for task, value in waits.items():
        if task not in tokens or type(value) is not int or not 0 <= value <= 3600:
            raise ValueError('Invalid LLM wait budget')
    return tokens, dict(waits)


def count(value):
    return value if type(value) is int and 0 <= value <= 10**9 else None


@dataclass(frozen=True)
class Response:
    text: str = ''
    kind: str = 'invalid_response'
    finish: str = 'unknown'
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    reasoning_present: bool = False


def parse_response(data):
    if not isinstance(data, dict):
        return Response()
    choices = data.get('choices')
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        return Response()
    choice = choices[0]
    message = choice.get('message')
    if not isinstance(message, dict):
        return Response()
    usage = data.get('usage')
    usage = usage if isinstance(usage, dict) else {}
    details = usage.get('completion_tokens_details')
    details = details if isinstance(details, dict) else {}
    finish = choice.get('finish_reason')
    finish = finish if finish in ('stop', 'length', 'tool_calls', 'content_filter') else 'unknown'
    metadata = dict(finish=finish, prompt_tokens=count(usage.get('prompt_tokens')),
                    completion_tokens=count(usage.get('completion_tokens')),
                    reasoning_tokens=count(details.get('reasoning_tokens')),
                    reasoning_present=any(bool(message.get(k)) for k in ('reasoning', 'reasoning_content')))
    content = message.get('content')
    if finish == 'length':
        return Response(kind='output_limit', **metadata)
    if finish in ('tool_calls', 'content_filter'):
        return Response(kind='unsupported_finish', **metadata)
    if content is None:
        content = ''
    if not isinstance(content, str):
        return Response(**metadata)
    if not content.strip():
        return Response(kind='reasoning_only' if metadata['reasoning_present'] else 'empty_response', **metadata)
    # Never republish an embedded chain of thought as editorial content.
    if '<think' in content.lower() or '</think' in content.lower():
        return Response(kind='reasoning_in_content', **metadata)
    if choice.get('finish_reason') not in (None, 'stop'):
        return Response(kind='unsupported_finish', **metadata)
    return Response(text=content.strip(), kind='success', **metadata)
