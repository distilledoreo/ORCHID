"""Token-counted compaction admission, serialized with native inference."""
from dataclasses import dataclass


class CapacityError(RuntimeError):
    pass


@dataclass(frozen=True)
class CompactionPolicy:
    high_water: float = 0.70
    safety_tokens: int = 128

    def __post_init__(self):
        if not 0 < self.high_water < 1 or self.safety_tokens < 0:
            raise ValueError('Invalid compaction scheduling policy')

    def prepare(self, session, question, *, incoming_tokens=0, recent_growth=0):
        """Compact at most once, before input/output or summarizer room runs out."""
        limits = session.limits
        query = len(session._query(question))
        reserve = limits.answer_tokens + self.safety_tokens
        growth = max(0, recent_growth, incoming_tokens)
        projected = max(len(session.student_ids), len(session.target_ids)) + incoming_tokens + query
        maximum = min(limits.student_context, limits.target_context)
        needed = projected + reserve + limits.expansion_tokens
        summary_needed = 0
        if hasattr(session.summarize, 'input_ids'):
            summary_needed = len(session.summarize.input_ids(session.summary_request(question)))
            summary_needed += session.summarize.output_tokens
        due = (needed > int(maximum * self.high_water)
               or summary_needed + 2 * growth + reserve > limits.target_context)
        if not due:
            return None
        if not session.entries:
            if projected + reserve > maximum:
                raise CapacityError('Input cannot fit an empty session; split source material into sections')
            return None
        if summary_needed > limits.target_context:
            raise CapacityError('Summary headroom is exhausted; refusing to truncate archived context')
        event = session.compact(question)
        projected = max(len(session.student_ids), len(session.target_ids)) + incoming_tokens + query
        if projected + reserve > maximum:
            raise CapacityError('Input cannot fit after compaction; split source material into sections')
        return event
