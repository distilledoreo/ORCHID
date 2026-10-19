"""Grounded edits: a change the target starts is redone with the archive text its draft points at."""
import re
import threading

from test_spec_session import Student, Tok, filler

from memory_gateway.specprefill import grounding
from memory_gateway.specprefill.spec_session import SpecLimits, SpecSession, write_paths

SEND = '    return Executors.mail(account).send(message)\n'


def module(lines=300):
    """A long file whose send_mail sits in the middle."""
    rows = [f'def helper_{i}(value):\n    return value + {i}\n' for i in range(lines // 4)]
    rows.insert(len(rows) // 2, 'def send_mail(account, message):\n' + SEND)
    return ''.join(rows)


FILE = module()


class PatchTarget:
    """Writes a patch to app/outgoing.py: from the real file when its send_mail is in the
    prompt, else from a guess. A continued prefix is respected."""
    stop_ids = ()

    def __init__(self, preface='Here is the change.\n', files=('app/outgoing.py',)):
        self.calls, self.preface, self.files = [], preface, files
        self.on_token, self.cancel = None, threading.Event()

    def body(self, prompt, path):
        if path == 'app/outgoing.py':
            if 'def send_mail(account, message):\n' + SEND in prompt:
                return ('@@ def send_mail(account, message):\n-' + SEND[1:]
                        + '+    return Executors.mail(account).send(message, retry=True)\n')
            return '@@ def send_mail(account, message):\n-    OutgoingExecutors().send(message)\n+    pass\n'
        grounded = 'NOTES_V2 = True' in prompt
        return '@@\n-NOTES_V2 = True\n+NOTES_V2 = False\n' if grounded else '@@\n-NOTES = guessed\n+NOTES = 1\n'

    def __call__(self, ids, maximum):
        self.calls.append(list(ids))
        text = Tok().decode(ids)
        opening = '<|im_start|>assistant\n<think>\n'
        answer = text[text.rfind(opening) + len(opening):]  # A continued prefix, if any.
        thinking = not answer.startswith('\n</think>\n\n')  # The prompt left the think block open.
        answer = answer if thinking else answer[len('\n</think>\n\n'):]
        prompt = text[:text.rfind('<|im_start|>assistant')]
        full = ('Planning the patch: *** Begin Patch is next.\n</think>\n\n' if thinking else '') + self.preface + '*** Begin Patch\n' + ''.join(
            f'*** Update File: {path}\n' + self.body(prompt, path) for path in self.files) + '*** End Patch\n'
        out = []
        for token in Tok().encode(full)[len(Tok().encode(answer)):][:maximum]:
            out.append(token)
            if self.on_token:
                self.on_token(token)
            if self.cancel.is_set():
                break
        return out


def make(target, **limits):
    return SpecSession(Tok(), Student(), target, system='Use excerpts.',
                       limits=SpecLimits(**{'student_context': 200000, 'target_context': 200000,
                                            'answer_tokens': 32, 'new_rate': 0.01, 'recall_tokens': 16,
                                            'patch_recent_tokens': 64, 'patch_region_lines': 6, **limits}))


def history(s, text=FILE):
    """A read of the file between unrelated parts; the sparse selection takes only the first part."""
    s.add_source('part-a', 1, filler(3000, 2))
    s.add_source('tool exec_command {"cmd": "cat app/outgoing.py"}', 1, text)
    for i in range(6):
        s.add_source(f'part-{i}', 1, filler(3000, 3 + i))


def test_a_patch_is_redone_with_the_region_its_draft_points_at():
    target = PatchTarget()
    streamed = []
    target.on_token = streamed.append
    s = make(target)
    history(s)
    out = Tok().decode(s.answer('Make the change.', maximum=400))
    assert 'send(message, retry=True)' in out and 'OutgoingExecutors' not in out
    assert out.startswith('Here is the change.\n*** Begin Patch\n*** Update File: app/outgoing.py')
    assert Tok().decode(streamed) == out  # The client saw only the grounded answer, once.
    [event] = s.events[-1]['grounded']
    assert event['path'] == 'app/outgoing.py' and event['regions'] == 1
    assert 0 < event['tokens'] < len(FILE) // 4  # The region around send_mail, not the whole file.
    shown = Tok().decode(target.calls[-1])
    assert 'def helper_0(' not in shown and 'def send_mail(account, message):' in shown
    assert len(target.calls) == 2


def test_each_file_of_a_patch_is_grounded():
    target = PatchTarget(files=('app/outgoing.py', 'app/notes.py'))
    s = make(target)
    history(s)
    s.add_source('tool exec_command {"cmd": "cat app/notes.py"}', 1, 'NOTES_V2 = True\n' + filler(400, 5))
    for i in range(3):
        s.add_source(f'part-z{i}', 1, filler(3000, 11 + i))
    out = Tok().decode(s.answer('Make the change.', maximum=600))
    assert 'retry=True' in out and 'NOTES_V2 = False' in out and 'guessed' not in out
    assert [g['path'] for g in s.events[-1]['grounded']] == ['app/outgoing.py', 'app/notes.py']
    assert len(target.calls) == 3


def test_an_answer_without_a_patch_is_untouched():
    streamed = []

    class Plain:
        stop_ids, cancel, on_token = (), threading.Event(), None

        def __call__(self, ids, maximum):
            out = Tok().encode('No change is needed. ***\n+++ done')[:maximum]
            for t in out:
                self.on_token(t)
            return out
    target = Plain()
    target.on_token = streamed.append
    s = make(target)
    history(s)
    assert Tok().decode(s.answer('Anything to change?', maximum=64)) == 'No change is needed. ***\n+++ done'
    assert Tok().decode(streamed) == 'No change is needed. ***\n+++ done' and s.events[-1]['grounded'] is None


def test_a_target_without_token_hooks_is_checked_after_the_call():
    target = PatchTarget()
    s = make(target)
    history(s)
    del target.on_token
    assert 'retry=True' in Tok().decode(s.answer('Make the change.', maximum=400))


class Writer:
    """Writes tests/test_outgoing.py; its call matches the real signature only when grounded."""
    stop_ids = ()

    def __init__(self):
        self.calls, self.on_token, self.cancel = [], None, threading.Event()

    def __call__(self, ids, maximum):
        self.calls.append(list(ids))
        text = Tok().decode(ids)
        prompt = text[:text.rfind('<|im_start|>assistant')]
        call = 'send_mail(account, message)' if 'def send_mail(account, message):' in prompt else 'send_mail(message)'
        full = ''.join(f'# line {i}\n' for i in range(3)) + 'from app.outgoing import send_mail\n' + ''.join(
            f'def test_{i}():\n    {call}\n' for i in range(8))
        out = []
        for token in Tok().encode(full)[:maximum]:
            out.append(token)
            if self.on_token:
                self.on_token(token)
            if self.cancel.is_set():
                break
        return out


def test_a_new_file_is_redone_with_the_definitions_it_imports():
    assert write_paths('Write the complete new file tests/test_outgoing.py for the task above.') == [
        'tests/test_outgoing.py']
    assert write_paths('What does the file say?') == []
    target, streamed = Writer(), []
    target.on_token = streamed.append
    s = make(target)
    history(s)
    out = Tok().decode(s.answer('Write the complete new file tests/test_outgoing.py for the task above.',
                                maximum=600))
    assert 'send_mail(account, message)' in out and 'send_mail(message)' not in out
    assert Tok().decode(streamed) == out
    [event] = s.events[-1]['grounded']
    assert event['kind'] == 'write' and event['regions'] == 1


def test_disabled_grounding_keeps_the_first_answer():
    target = PatchTarget()
    s = make(target, patch_tokens=0)
    history(s)
    assert 'OutgoingExecutors' in Tok().decode(s.answer('Make the change.', maximum=400))
    assert len(target.calls) == 1


def test_grounding_evidence_is_shown_once_and_not_kept():
    target = PatchTarget()
    s = make(target)
    history(s)
    s.answer('Make the change.', maximum=400)
    assert 'def send_mail(account, message):\n' + SEND in Tok().decode(target.calls[-1])
    kept = Tok().decode(s.target_ids)
    assert 'def send_mail(account, message):\n' + SEND not in kept.split('Here is the change.')[0]
    assert 'retry=True' in kept and s.events[-1]['ephemeral_chunks'] > 0  # The reply stays.


def test_watch_holds_back_a_line_that_could_be_a_header():
    sent = []

    class T:
        on_token, cancel = sent.append, threading.Event()
    watch = grounding.Watch(Tok(), T)
    for t in Tok().encode('ok\n*** Begin'):
        watch(t)
    assert Tok().decode(sent) == 'ok\n'  # '*** Begin' could still start a patch.
    assert watch.finish([]) is False and Tok().decode(sent) == 'ok\n*** Begin'


def test_anchors_of_drafts():
    lines, names, _ = grounding.anchors(
        '*** Update File: a.py\n@@ def send_mail(account, message):\n-    return old_value(x)\n+    new()\n', 'update')
    assert lines == ['def send_mail(account, message):', 'return old_value(x)'] and names == ['send_mail']
    _, names, modules = grounding.anchors('"""Doc."""\nfrom .mail import Mail, MailError\nimport os\n', 'write')
    assert names == ['Mail', 'MailError'] and modules == ['mail', 'os']


def test_the_gateway_streams_one_grounded_patch(tmp_path):
    from test_specprefill_gateway_auto import chat, reply, service
    s, resources = service(tmp_path, new_rate=0.01, recall_tokens=16)
    resources.target = PatchTarget()
    messages = [('user', filler(3000, 2)), ('assistant', 'Reading the file.'),
                ('user', 'cat app/outgoing.py\n' + FILE)]
    for i in range(4):
        messages += [('assistant', 'OK'), ('user', filler(3000, 3 + i))]
    streamed = []
    result = s.complete(None, None, None, chat(*messages, ('assistant', 'OK'), ('user', 'Make the change.')),
                        on_text=lambda text, kind='content': streamed.append(text))
    assert 'send(message, retry=True)' in reply(result) and 'OutgoingExecutors' not in reply(result)
    assert ''.join(streamed) == 'Planning the patch: *** Begin Patch is next.\n' + reply(result)  # Reasoning, then the answer.
    assert result['choices'][0]['message']['reasoning_content'] == 'Planning the patch: *** Begin Patch is next.'


def test_whole_history_requests_ignore_quoted_material():
    from memory_gateway.specprefill.spec_session import is_broad
    assert is_broad('Summarize this session so far.') and is_broad('List every file you edited.')
    assert not is_broad('In the output of `cat x.py`, quote the line that starts with: store.save(summary("new"')
    assert not is_broad('What does `summarize_all()` return?')


def test_negated_summaries_are_not_whole_history_requests():
    from memory_gateway.specprefill.spec_session import is_broad
    assert not is_broad('Read each file in full, one piece per command, without summarizing between pieces.')
    assert not is_broad("Don't summarize, just fix the bug.") and not is_broad('Give no summary.')
    assert is_broad('Now summarize what we changed.')


def test_a_patch_is_made_exact_against_the_archive():
    archive = ['def send_mail(account, message):', '    payload = build(message)',
               '    return Executors.mail(account).send(payload)', '', 'def other():', '    pass']
    patch = ('*** Begin Patch\n*** Update File: app/outgoing.py\n@@ def send_mail(account, message):\n'
             '-    return Executors.mail(account).send(payload )\n'  # A small slip: the real line differs.
             '+    return Executors.mail(account).send(payload, retry=True)\n'
             ' def other_thing():\n'  # Not in the file: dropped.
             '*** End Patch')
    fixed, stats = grounding.align_patch(patch, [archive, ['unrelated']])
    assert stats == {'hunks': 1, 'aligned': 1, 'lines_fixed': 2}
    assert '-    return Executors.mail(account).send(payload)\n' in fixed and 'other_thing' not in fixed
    assert '+    return Executors.mail(account).send(payload, retry=True)' in fixed
    # Nothing found: the hunk is left as written.
    untouched, stats = grounding.align_patch(patch, [['nothing like it']])
    assert untouched == patch and stats['aligned'] == 0


def test_numbered_reads_align_without_their_line_numbers():
    archive = ['   12\tdef send_mail(account, message):', '   13\t    return old(message)']
    patch = ('*** Begin Patch\n*** Update File: a.py\n@@\n def send_mail(account, message):\n'
             '-    return old(message )\n+    return new(message)\n*** End Patch')
    fixed, stats = grounding.align_patch(patch, [archive])
    assert stats['aligned'] == 1 and '-    return old(message)\n' in fixed and '12\t' not in fixed


def test_a_turn_that_is_only_what_the_model_asked_for_skips_the_student():
    from test_spec_session import Target
    student = Student()
    s = SpecSession(Tok(), student, Target(), system='Use excerpts.',
                    limits=SpecLimits(student_context=200000, target_context=200000, answer_tokens=32, new_rate=0.01))
    s.add_source('part-a', 1, filler(3000, 2))
    s.answer('Return KEYQ')
    calls = len(student.calls)
    index = s.add_source('tool read_file a.py', 1, 'KEYZ=77777 ' + filler(800, 5))
    assert Tok().decode(s.answer('Return KEYZ', fresh=[index])) == '77777'
    assert len(student.calls) == calls and s.events[-1]['verbatim']  # Straight to the target.
    assert 'KEYZ=77777 ' + filler(800, 5) in Tok().decode(s.target_ids)  # In full.
    s.add_source('part-b', 1, filler(3000, 7))  # Not asked for: selection as before.
    s.answer('Return KEYQ')
    assert len(student.calls) == calls + 1 and not s.events[-1]['verbatim']


class IntentStudent(Student):
    """Scores lines naming whatever the last reply asked for (after 'Its last step:')."""

    def trace(self, ids, keep_ratio):
        self.calls.append(list(ids))
        text = Tok().decode(ids)
        scores = [0.01] * len(ids)
        at = text.rfind('Its last step:')
        if at >= 0:
            for word in re.findall(r'want_(\w+)', text[at:]):
                pos = text.find(f'def {word}(')
                while pos >= 0:
                    for p in range(pos, min(len(ids), pos + 40)):
                        scores[p] = 1.0
                    pos = text.find(f'def {word}(', pos + 1)
        return {'scores': scores, 'reused_prefix_tokens': 0}


def test_intent_mode_shows_the_part_of_a_long_tool_result_the_last_step_wanted():
    from test_spec_session import Target
    s = SpecSession(Tok(), IntentStudent(), Target(), system='Use excerpts.',
                    limits=SpecLimits(student_context=200000, target_context=200000, answer_tokens=64,
                                      new_rate=0.01, fresh_mode='intent', fresh_small=200, fresh_floor=64,
                                      fresh_rate=0.1))
    s.add_source('part-a', 1, filler(3000, 2))
    s.answer('Return KEYQ')
    s.history[-1] = (s.history[-1][0], Tok().encode('I will read a.py to find want_send_mail.\n'))
    body = ''.join(f'def helper_{i}(x):\n    return x + {i}\n' for i in range(60))
    body = body[:1500] + 'def send_mail(account):\n    return KEYM=31415\n' + body[1500:]
    index = s.add_source('tool exec_command cat a.py', 1, body)
    s.answer('Continue the task using the tool result above. Return KEYM', fresh=[index])
    shown = Tok().decode(s.target_ids)
    assert 'def send_mail(account):' in shown and 'shown in part' in shown
    assert body not in shown and len(shown) < len(body) + 6000  # Not the whole result.
    # Asked for again (the same output): in full.
    s.answer('Continue the task using the tool result above. Return KEYM', fresh=[index])
    assert body in Tok().decode(s.target_ids)
