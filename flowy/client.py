import sys
import json
import uuid
import logging

from boto.swf.layer1 import Layer1
from boto.swf.layer1_decisions import Layer1Decisions
from boto.swf.exceptions import SWFTypeAlreadyExistsError, SWFResponseError

from flowy.workflow import _UnhandledActivityError


__all__ = ['ActivityClient', 'WorkflowClient']


class SWFClient(object):
    def __init__(self, domain, task_list, client=None):
        self.client = client if client is not None else Layer1()
        self.domain = domain
        self.task_list = task_list
        self._scheduled_activities = []

    def register_workflow(self, name, version, workflow_runner,
                          execution_start_to_close=3600,
                          task_start_to_close=60,
                          child_policy='TERMINATE',
                          doc=None):
        version = str(version)
        execution_start_to_close = str(execution_start_to_close)
        task_start_to_close = str(task_start_to_close)
        try:
            self.client.register_workflow_type(self.domain, name, version,
                                               self.task_list, child_policy,
                                               execution_start_to_close,
                                               task_start_to_close, doc)
            logging.info("Registered workflow: %s %s", name, version)
        except SWFTypeAlreadyExistsError:
            logging.warning("Workflow already registered: %s %s",
                            name, version)
            reg_w = self.client.describe_workflow_type(self.domain, name,
                                                       version)
            conf = reg_w['configuration']
            reg_estc = conf['defaultExecutionStartToCloseTimeout']
            reg_tstc = conf['defaultTaskStartToCloseTimeout']
            reg_tl = conf['defaultTaskList']['name']
            reg_cp = conf['defaultChildPolicy']

            if (reg_estc != execution_start_to_close
                    or reg_tstc != task_start_to_close
                    or reg_tl != self.task_list
                    or reg_cp != child_policy):
                logging.critical("Registered workflow "
                                 "has different defaults: %s %s",
                                 name, version)
                sys.exit(1)

    def queue_activity(self, call_id, name, version, input,
                       heartbeat=None,
                       schedule_to_close=None,
                       schedule_to_start=None,
                       start_to_close=None,
                       task_list=None):
        self._scheduled_activities.append((
            (str(call_id), name, str(version)),
            {
                'heartbeat_timeout': _str_or_none(heartbeat),
                'schedule_to_close_timeout': _str_or_none(schedule_to_close),
                'schedule_to_start_timeout': _str_or_none(schedule_to_start),
                'start_to_close_timeout': _str_or_none(start_to_close),
                'task_list': task_list,
                'input': input
            }
        ))

    def schedule_activities(self, token, context=None):
        d = Layer1Decisions()
        for args, kwargs in self._scheduled_activities:
            d.schedule_activity_task(*args, **kwargs)
            name, version = args[1:]
            logging.info("Scheduled activity: %s %s", name, version)
        data = d._data
        try:
            self.client.respond_decision_task_completed(token, data, context)
        except SWFResponseError:
            logging.warning("Cannot send decisions: %s", token)
        self._scheduled_activities = []

    def complete_workflow(self, token, result):
        d = Layer1Decisions()
        d.complete_workflow_execution(result=result)
        data = d._data
        try:
            self.client.respond_decision_task_completed(token, decisions=data)
            logging.info("Completed workflow: %s %s", token, result)
        except SWFResponseError:
            logging.warning("Cannot complete workflow: %s", token)

    def terminate_workflow(self, workflow_id, reason):
        try:
            self.client.terminate_workflow_execution(self.domain, workflow_id,
                                                     reason=reason)
            logging.info("Terminated workflow: %s %s", workflow_id, reason)
        except SWFResponseError:
            logging.warning("Cannot terminate workflow: %s", workflow_id)

    def poll_workflow(self, next_page_token=None):
        poll = self.client.poll_for_decision_task
        while 1:
            try:
                return poll(self.domain, self.task_list, reverse_order=True,
                            next_page_token=next_page_token)
            except (IOError, SWFResponseError):
                logging.warning("Unknown error when pulling decisions: %s %s",
                                self.domain, self.task_list)

    def request_workflow(self):
        return WorkflowResponse(self)

    def register_activity(self, name, version, activity_runner,
                          heartbeat=60,
                          schedule_to_close=420,
                          schedule_to_start=120,
                          start_to_close=300,
                          doc=None):
        schedule_to_close = str(schedule_to_close)
        schedule_to_start = str(schedule_to_start)
        start_to_close = str(start_to_close)
        heartbeat = str(heartbeat)
        try:
            self.client.register_activity_type(self.domain, name, version,
                                               self.task_list, heartbeat,
                                               schedule_to_close,
                                               schedule_to_start,
                                               start_to_close, doc)
            logging.info("Registered activity: %s %s", name, version)
        except SWFTypeAlreadyExistsError:
            logging.warning("Activity already registered: %s %s",
                            name, version)
            reg_a = self.client.describe_activity_type(self.domain, name,
                                                       version)
            conf = reg_a['configuration']
            reg_tstc = conf['defaultTaskStartToCloseTimeout']
            reg_tsts = conf['defaultTaskScheduleToStartTimeout']
            reg_tschtc = conf['defaultTaskScheduleToCloseTimeout']
            reg_hb = conf['defaultTaskHeartbeatTimeout']
            reg_tl = conf['defaultTaskList']['name']

            if (reg_tstc != start_to_close
                    or reg_tsts != schedule_to_start
                    or reg_tschtc != schedule_to_close
                    or reg_hb != heartbeat
                    or reg_tl != self.task_list):
                logging.critical("Registered activity "
                                 "has different defaults: %s %s",
                                 name, version)
                sys.exit(1)

    def poll_activity(self):
        poll = self.client.poll_for_activity_task
        while 1:
            try:
                return poll(self.domain, self.task_list)
            except (IOError, SWFResponseError):
                logging.warning("Unknown error when pulling activities: %s %s",
                                self.domain, self.task_list)

    def complete_activity(self, token, result):
        try:
            self.client.respond_activity_task_completed(token, result)
            logging.info("Completed activity: %s %s", token, result)
        except SWFResponseError:
            logging.warning("Cannot complete activity: %s", token)

    def terminate_activity(self, token, reason):
        try:
            self.client.respond_activity_task_failed(token, reason=reason)
            logging.info("Terminated activity: %s %s", token, reason)
        except SWFResponseError:
            logging.warning("Cannot terminate activity: %s", token)

    def heartbeat(self, token):
        try:
            self.client.record_activity_task_heartbeat(token)
            logging.info("Sent activity heartbeat: %s", token)
        except SWFResponseError:
            logging.warning("Error when sending activity heartbeat: %s", token)
            return False
        return True

    def request_activity(self):
        return ActivityResponse(self)

    def start_workflow(self, name, version, input):
        try:
            self.client.start_workflow_execution(self.domain,
                                                 str(uuid.uuid4()),
                                                 name, str(version),
                                                 task_list=self.task_list,
                                                 input=input)
        except SWFResponseError:
            return False
        return True


class WorkflowResponse(object):
    def __init__(self, client):
        self.client = client
        self._event_to_call_id = {}
        self._retries = {}
        self._scheduled = set()
        self._results = {}
        self._timed_out = set()
        self._with_errors = {}
        self.input = None

        response = {}
        while 'taskToken' not in response or not response['taskToken']:
            response = self.client.poll_workflow()
        self._api_response = response

        self._restore_context()

        # Update the context with all new events
        for new_event in self._new_events:
            event_type = new_event['eventType']
            getattr(self, '_%s' % event_type, lambda x: 0)(new_event)

        # Assuming workflow started is always the first event
        assert self.input is not None

    @property
    def name(self):
        return self._api_response['workflowType']['name']

    @property
    def version(self):
        return self._api_response['workflowType']['version']

    def queue_activity(
        self, call_id, name, version, input,
        heartbeat=None,
        schedule_to_close=None,
        schedule_to_start=None,
        start_to_close=None,
        task_list=None,
        retries=3
    ):
        self.client.queue_activity(
            call_id, name, version, input,
            heartbeat=heartbeat,
            schedule_to_close=schedule_to_close,
            schedule_to_start=schedule_to_start,
            start_to_close=start_to_close,
            task_list=task_list
        )
        self._retries.setdefault(call_id, retries + 1)

    def schedule_activities(self):
        self.client.schedule_activities(self._token, self._serialize_context())

    def complete_workflow(self, result):
        self.client.complete_workflow(self._token, result)

    def terminate_workflow(self, reason):
        workflow_id = self._api_response['workflowExecution']['workflowId']
        self.client.terminate_workflow(workflow_id, reason)

    def any_activity_running(self):
        return bool(self._scheduled)

    def is_activity_scheduled(self, call_id):
        return call_id in self._scheduled

    def activity_result(self, call_id, default=None):
        return self._results.get(call_id, default)

    def activity_error(self, call_id, default=None):
        return self._with_errors.get(call_id, default)

    def is_activity_timedout(self, call_id):
        return call_id in self._timed_out

    def should_retry(self, call_id):
        return self._retries[call_id] > 0

    def _ActivityTaskScheduled(self, event):
        event_id = event['eventId']
        subdict = event['activityTaskScheduledEventAttributes']
        call_id = int(subdict['activityId'])
        self._event_to_call_id[event_id] = call_id
        self._scheduled.add(call_id)
        if call_id in self._timed_out:
            self._timed_out.remove(call_id)

    def _ActivityTaskCompleted(self, event):
        subdict = event['activityTaskCompletedEventAttributes']
        event_id, result = subdict['scheduledEventId'], subdict['result']
        self._scheduled.remove(self._event_to_call_id[event_id])
        self._results[self._event_to_call_id[event_id]] = result

    def _ActivityTaskFailed(self, event):
        subdict = event['activityTaskFailedEventAttributes']
        event_id, reason = subdict['scheduledEventId'], subdict['reason']
        self._with_errors[self._event_to_call_id[event_id]] = reason

    def _ActivityTaskTimedOut(self, event):
        subdict = event['activityTaskTimedOutEventAttributes']
        event_id = subdict['scheduledEventId']
        self._scheduled.remove(self._event_to_call_id[event_id])
        self._timed_out.add(self._event_to_call_id[event_id])
        self._retries[self._event_to_call_id[event_id]] -= 1

    def _WorkflowExecutionStarted(self, event):
        subdict = event['workflowExecutionStartedEventAttributes']
        self.input = subdict['input']

    def _restore_context(self):
        if self._context is not None:
            try:
                initial_state = json.loads(self._context)
                self._event_to_call_id = self._fix_keys(
                    initial_state['event_to_call_id']
                )
                self._retries = self._fix_keys(initial_state['retries'])
                self._scheduled = set(initial_state['scheduled'])
                self._results = self._fix_keys(initial_state['results'])
                self._timed_out = set(initial_state['timed_out'])
                self._with_errors = self._fix_keys(
                    initial_state['with_errors']
                )
                self.input = initial_state['input']
            except (ValueError, KeyError):
                logging.critical("Cannot load context: %s" % self._context)
                sys.exit(1)

    def _serialize_context(self):
        return json.dumps({
            'event_to_call_id': self._event_to_call_id,
            'retries': self._retries,
            'scheduled': list(self._scheduled),
            'results': self._results,
            'timed_out': list(self._timed_out),
            'with_errors': self._with_errors,
            'input': self.input,
        })

    @property
    def _token(self):
        return self._api_response['taskToken']

    @property
    def _context(self):
        for event in self._new_events:
            if event['eventType'] == 'DecisionTaskCompleted':
                DTCEA = 'decisionTaskCompletedEventAttributes'
                return event[DTCEA]['executionContext']
        return None

    @property
    def _events(self):
        if not hasattr(self, '_cached_events'):
            events = []
            api_response = self._api_response
            while api_response.get('nextPageToken'):
                for event in api_response['events']:
                    events.append(event)
                api_response = self.client.poll_workflow(
                    next_page_token=api_response['nextPageToken']
                )
            for event in api_response['events']:
                events.append(event)
            self._cached_events = events
        return self._cached_events

    @property
    def _new_events(self):
        decisions_completed = 0
        events = []
        prev_id = self._api_response.get('previousStartedEventId')
        for event in self._events:
            if event['eventType'] == 'DecisionTaskCompleted':
                decisions_completed += 1
            if prev_id and event['eventId'] == prev_id:
                break
            events.append(event)
        assert decisions_completed <= 1
        return reversed(events)

    @staticmethod
    def _fix_keys(d):
        # Fix json's stupid silent key conversion from int to string
        return dict((int(key), value) for key, value in d.items())


class WorkflowClient(object):
    def __init__(self):
        self._workflows = {}
        self._register_queue = []

    def register(self, name, version, workflow_runner,
                 execution_start_to_close=3600, task_start_to_close=60,
                 child_policy='TERMINATE', doc=None):
        self._workflows[(name, str(version))] = workflow_runner
        self._register_queue.append((name, version, workflow_runner,
                                     execution_start_to_close,
                                     task_start_to_close,
                                     child_policy, doc))

    def start(self, client):
        for args in self._register_queue:
            if not client.register_workflow(*args):
                sys.exit(1)
        while 1:
            response = client.request_workflow()
            logging.info("Processing workflow: %s %s",
                         response.name, response.version)
            workflow_runner = self._query(response.name, response.version)
            if workflow_runner is None:
                logging.warning("No workflow registered for: %s %s",
                                response.name, response.version)
                continue
            try:
                result, activities = workflow_runner.resume(
                    response.input, response
                )
            except _UnhandledActivityError as e:
                logging.warning("Stopped workflow because of an exception"
                                " inside an activity: %s", e.message)
                response.terminate_workflow(e.message)
            except Exception as e:
                logging.warning("Stopped workflow because of an unhandled"
                                " exception: %s", e.message)
                response.terminate_workflow(e.message)
            else:
                activities_running = response.any_activity_running()
                activities_scheduled = bool(activities)
                if activities_running or activities_scheduled:
                    for a in activities:
                        response.queue_activity(
                            a.call_id, a.name, a.version, a.input,
                            heartbeat=a.options.heartbeat,
                            schedule_to_close=a.options.schedule_to_close,
                            schedule_to_start=a.options.schedule_to_start,
                            start_to_close=a.options.start_to_close,
                            task_list=a.options.task_list,
                            retries=a.options.retry
                        )
                    response.schedule_activities()
                else:
                    response.complete_workflow(result)

    def __call__(self, name, version, *args, **kwargs):
        optional_args = [
            'execution_start_to_close',
            'task_start_to_close',
            'child_policy',
        ]
        r_kwargs = {}
        for arg_name in optional_args:
            arg_value = kwargs.pop(arg_name, None)
            if arg_value is not None:
                r_kwargs[arg_name] = arg_value

        def wrapper(workflow):
            r_kwargs['doc'] = workflow.__doc__.strip()
            self.register(name, version, workflow(*args, **kwargs), **r_kwargs)
            return workflow

        return wrapper

    def schedule(self, name, version, *args, **kwargs):
        input = self.serialize_workflow_arguments(*args, **kwargs)
        return self.client.start_workflow(name, version, input)

    @staticmethod
    def serialize_workflow_arguments(*args, **kwargs):
        return json.dumps({"args": args, "kwargs": kwargs})

    def _query(self, name, version):
        return self._workflows.get((name, version))


class ActivityResponse(object):
    def __init__(self, client):
        self.client = client
        response = self.client.poll_activity()
        while 'taskToken' not in response or not response['taskToken']:
            response = self.client.poll_activity()
        self._api_response = response

    def complete(self, result):
        self.client.complete_activity(self._token, result)

    def terminate(self, reason):
        self.client.terminate_activity(self._token, reason)

    def heartbeat(self):
        return self.client.heartbeat(self._token)

    @property
    def name(self):
        return self._api_response['activityType']['name']

    @property
    def version(self):
        return self._api_response['activityType']['version']

    @property
    def input(self):
        return self._api_response['input']

    @property
    def _token(self):
        return self._api_response['taskToken']


class ActivityClient(object):
    def __init__(self):
        self._activities = {}
        self._register_queue = []

    def register(self, name, version, activity_runner,
                 heartbeat=60, schedule_to_close=420, schedule_to_start=120,
                 start_to_close=300, doc=None):
        # All versions are converted to string in SWF and that's how we should
        # store them too in order to be able to query for them
        self._activities[(name, str(version))] = activity_runner
        self._register_queue.append((name, version, activity_runner, heartbeat,
                                     schedule_to_close, schedule_to_start,
                                     start_to_close, doc))

    def start_on(self, domain, task_list, client=None):
        client = SWFClient(domain, task_list, client)
        return self.start(client)

    def start(self, client):
        for args in self._register_queue:
            if not client.register_activity(*args):
                sys.exit(1)
        while 1:
            response = client.request_activity()
            logging.info("Processing activity: %s %s",
                         response.name, response.version)
            activity_runner = self._query(response.name, response.version)
            if activity_runner is None:
                logging.warning("No activity registered for: %s %s",
                                response.name, response.version)
                continue
            try:
                result = activity_runner.call(response.input, response)
            except Exception as e:
                response.terminate(e.message)
            else:
                response.complete(result)

    def __call__(self, name, version, *args, **kwargs):
        version = str(version)
        optional_args = [
            'heartbeat',
            'schedule_to_close',
            'schedule_to_start',
            'start_to_close',
        ]
        r_kwargs = {}
        for arg_name in optional_args:
            arg_value = kwargs.pop(arg_name, None)
            if arg_value is not None:
                r_kwargs[arg_name] = arg_value

        def wrapper(activity):
            r_kwargs['doc'] = activity.__doc__.strip()
            self.register(
                name, version, activity(*args, **kwargs), **r_kwargs
            )
            return activity

        return wrapper

    def _query(self, name, version):
        # XXX: if we can't resolve this activity log the error and continue
        return self._activities.get((name, version))


workflow_client = WorkflowClient()
activity_client = ActivityClient()


def _str_or_none(maybe_none):
    if maybe_none is not None:
        return str(maybe_none)
