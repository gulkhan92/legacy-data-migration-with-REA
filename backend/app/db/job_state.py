from app.db.models.enums import JobStatus

S = JobStatus

ALLOWED_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    S.CREATED: frozenset({S.UPLOADED, S.FAILED}),
    S.UPLOADED: frozenset({S.DISCOVERING, S.FAILED}),
    S.DISCOVERING: frozenset({S.PROFILED, S.FAILED}),
    S.PROFILED: frozenset({S.MAPPED, S.FAILED}),
    S.MAPPED: frozenset({S.AWAITING_REVIEW, S.FAILED}),
    S.AWAITING_REVIEW: frozenset({S.DRY_RUN, S.FAILED}),
    S.DRY_RUN: frozenset({S.READY, S.AWAITING_REVIEW, S.FAILED}),
    S.READY: frozenset({S.LOADING, S.AWAITING_REVIEW, S.FAILED}),
    S.LOADING: frozenset({S.RECONCILING, S.FAILED}),
    S.RECONCILING: frozenset({S.COMPLETED, S.FAILED}),
    S.COMPLETED: frozenset({S.ROLLED_BACK}),
    S.FAILED: frozenset({S.ROLLED_BACK}),
    S.ROLLED_BACK: frozenset(),
}


class InvalidTransition(ValueError):
    def __init__(self, current: JobStatus, target: JobStatus) -> None:
        super().__init__(f"Job cannot move from {current.value} to {target.value}")
        self.current = current
        self.target = target


def check_transition(current: JobStatus, target: JobStatus) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidTransition(current, target)
