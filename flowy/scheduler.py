from contextlib import contextmanager

from flowy import str_or_none, posint_or_none, posint
from flowy.result import Placeholder, Error, Result


_sentinel = object()


class OptionsScheduler(object):
    def __init__(self, scheduler):
        self._scheduler = scheduler
        self._activity_options_stack = [dict()]
        self._subworkflow_options_stack = [dict()]

    def remote_activity(self, task_id, args, kwargs,
                        args_serializer, result_deserializer,
                        heartbeat=None,
                        schedule_to_close=None,
                        schedule_to_start=None,
                        start_to_close=None,
                        task_list=None,
                        retry=3,
                        delay=0,
                        error_handling=False):
        options = dict(
            heartbeat=posint_or_none(heartbeat),
            schedule_to_close=posint_or_none(schedule_to_close),
            schedule_to_start=posint_or_none(schedule_to_start),
            start_to_close=posint_or_none(start_to_close),
            task_list=str_or_none(task_list),
            retry=posint(retry),
            delay=posint(delay),
            error_handling=bool(error_handling)
        )
        options.update(self._activity_options_stack[-1])
        self._scheduler.remote_activity(
            task_id=task_id, args=args, kwargs=kwargs,
            args_serializer=args_serializer,
            result_deserializer=result_deserializer,
            **options
        )

    def remote_subworkflow(self, task_id, args, kwargs,
                           args_serializer, result_deserializer,
                           workflow_duration=None,
                           decision_duration=None,
                           task_list=None,
                           retry=3,
                           delay=0,
                           error_handling=False):
        options = dict(
            workflow_duration=posint_or_none(workflow_duration),
            decision_duration=posint_or_none(decision_duration),
            task_list=str_or_none(task_list),
            retry=posint(retry),
            delay=posint(delay),
            error_handling=bool(error_handling)
        )
        options.update(self._subworkflow_options_stack[-1])
        self._scheduler.remote_subworkflow(
            task_id=task_id, args=args, kwargs=kwargs,
            args_serializer=args_serializer,
            result_deserializer=result_deserializer,
            **options
        )

    @contextmanager
    def options(self,
                heartbeat=_sentinel,
                schedule_to_close=_sentinel,
                schedule_to_start=_sentinel,
                start_to_close=_sentinel,
                workflow_duration=_sentinel,
                decision_duration=_sentinel,
                task_list=_sentinel,
                retry=_sentinel,
                delay=_sentinel,
                error_handling=_sentinel):
        a_options = dict()
        s_options = dict()
        if heartbeat is not _sentinel:
            a_options['heartbeat'] = posint_or_none(heartbeat)
        if schedule_to_close is not _sentinel:
            a_options['schedule_to_close'] = posint_or_none(schedule_to_close)
        if schedule_to_start is not _sentinel:
            a_options['schedule_to_start'] = posint_or_none(schedule_to_start)
        if start_to_close is not _sentinel:
            a_options['start_to_close'] = posint_or_none(start_to_close)
        if workflow_duration is not _sentinel:
            s_options['workflow_duration'] = posint_or_none(workflow_duration)
        if decision_duration is not _sentinel:
            s_options['decision_duration'] = posint_or_none(decision_duration)
        if task_list is not _sentinel:
            a_options['task_list'] = str_or_none(task_list)
            s_options['task_list'] = str_or_none(task_list)
        if retry is not _sentinel:
            a_options['retry'] = max(int(retry), 0)
            s_options['retry'] = max(int(retry), 0)
        if delay is not _sentinel:
            a_options['delay'] = max(int(delay), 0)
            s_options['delay'] = max(int(delay), 0)
        if error_handling is not _sentinel:
            a_options['error_handling'] = bool(error_handling)
            s_options['error_handling'] = bool(error_handling)
        self._activity_options_stack.append(
            dict(self._activity_options_stack[-1], **a_options)
        )
        self._subworkflow_options_stack.append(
            dict(self._subworkflow_options_stack[-1], **s_options)
        )
        yield
        self._activity_options_stack.pop()
        self._subworkflow_options_stack.pop()


class ArgsDependencyScheduler(object):
    def __init__(self, scheduler):
        self._scheduler = scheduler

    def remote_activity(self, task_id, args, kwargs,
                        args_serializer, result_deserializer,
                        heartbeat, schedule_to_close,
                        schedule_to_start, start_to_close,
                        task_list, retry, delay, error_handling):
        result = self._args_based_result(args, kwargs, error_handling)
        if result is not None:
            return result
        return self._scheduler.remote_activity(
            task_id=task_id,
            input=self._serialize_args(args, kwargs, args_serializer),
            result_deserializer=result_deserializer,
            heartbeat=heartbeat,
            schedule_to_close=schedule_to_close,
            schedule_to_start=schedule_to_start,
            start_to_close=start_to_close,
            task_list=task_list,
            retry=retry,
            delay=delay,
            error_handling=error_handling
        )

    def remote_subworkflow(self, task_id, args, kwargs,
                           args_serializer, result_deserializer,
                           workflow_duration, decision_duration,
                           task_list, retry, delay, error_handling):
        result = self._args_based_result(args, kwargs, error_handling)
        if result is not None:
            return result
        return self._scheduler.remote_subworkflow(
            task_id=task_id,
            input=self._serialize_args(args, kwargs, args_serializer),
            result_deserializer=result_deserializer,
            workflow_duration=workflow_duration,
            decision_duration=decision_duration,
            task_list=task_list,
            retry=retry,
            delay=delay,
            error_handling=error_handling
        )

    def _args_based_result(self, args, kwargs, error_handling):
        args = tuple(args) + tuple(kwargs.values())
        if self._deps_in_args(args):
            return Placeholder()
        errs = self._errs_in_args(args)
        if errs:
            composed_err = "\n".join(e._reason for e in errs)
            if error_handling:
                return Error(composed_err)
            else:
                self._scheduler.fail(reason=composed_err)
                return Placeholder()

    def _deps_in_args(self, args):
        return any(isinstance(r, Placeholder) for r in args)

    def _errs_in_args(self, args):
        return list(filter(lambda x: isinstance(x, Error), args))

    def _serialize_args(self, args, kwargs, args_serializer):
        raw_args = [
            arg.result() if isinstance(arg, Result) else arg for arg in args
        ]
        raw_kwargs = dict(
            (k, v.result() if isinstance(v, Result) else v)
            for k, v in kwargs.items()
        )
        return args_serializer(raw_args, raw_kwargs)
