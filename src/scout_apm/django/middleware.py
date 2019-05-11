# coding=utf-8
from __future__ import absolute_import, division, print_function, unicode_literals

import logging
import re
from datetime import datetime

from scout_apm.api.context import Context
from scout_apm.core.ignore import ignore_path
from scout_apm.core.remote_ip import RemoteIp
from scout_apm.core.tracked_request import TrackedRequest

logger = logging.getLogger(__name__)

queue_time_re = re.compile(r"(t=|\.)")


def track_request_queue_time(request, tracked_request):
    header_value = request.META.get("HTTP_X_QUEUE_START") or request.META.get(
        "HTTP_X_REQUEST_START"
    )
    if not header_value:
        return

    raw_start = queue_time_re.sub("", header_value)
    try:
        timestamp = float(raw_start[0:10] + "." + raw_start[10:13])
    except ValueError:
        return

    parsed_start = datetime.utcfromtimestamp(timestamp)
    # Ignore if in the future
    if parsed_start > tracked_request.start_time:
        return

    queue_time = tracked_request.start_time - parsed_start
    queue_time_us = int(queue_time.total_seconds() * 1000000)
    tracked_request.tag("scout.queue_time_us", queue_time_us)


class MiddlewareTimingMiddleware(object):
    """
    Insert as early into the Middleware stack as possible (outermost layers),
    so that other middlewares called after can be timed.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tracked_request = TrackedRequest.instance()

        tracked_request.start_span(operation="Middleware")
        track_request_queue_time(request, tracked_request)

        try:
            return self.get_response(request)
        finally:
            TrackedRequest.instance().stop_span()


class ViewTimingMiddleware(object):
    """
    Insert as deep into the middleware stack as possible, ideally wrapping no
    other middleware. Designed to time the View itself
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        """
        Wrap a single incoming request with start and stop calls.
        This will start timing, but relies on the process_view callback to
        capture more details about what view was really called, and other
        similar info.

        If process_view isn't called, then the request will not
        be recorded.  This can happen if a middleware further along the stack
        doesn't call onward, and instead returns a response directly.
        """

        tr = TrackedRequest.instance()
        tr.mark_real_request()

        # This operation name won't be recorded unless changed later in
        # process_view
        operation = "Unknown"
        tr.start_span(operation=operation)
        try:
            response = self.get_response(request)
        finally:
            tr.stop_span()
        return response

    def process_view(self, request, view_func, view_args, view_kwargs):
        """
        Capture details about the view_func that is about to execute
        """
        try:
            if ignore_path(request.path):
                TrackedRequest.instance().tag("ignore_transaction", True)

            view_name = request.resolver_match._func_path
            span = TrackedRequest.instance().current_span()
            if span is not None:
                span.operation = "Controller/" + view_name
                Context.add("path", request.path)
                Context.add("user_ip", RemoteIp.lookup_from_headers(request.META))
                if getattr(request, "user", None) is not None:
                    Context.add("username", request.user.get_username())
        except Exception:
            pass

    def process_exception(self, request, exception):
        """
        Mark this request as having errored out

        Does not modify or catch or otherwise change the exception thrown
        """
        TrackedRequest.instance().tag("error", "true")


class OldStyleMiddlewareTimingMiddleware(object):
    """
    Insert as early into the Middleware stack as possible (outermost layers),
    so that other middlewares called after can be timed.
    """

    def process_request(self, request):
        tracked_request = TrackedRequest.instance()
        span = tracked_request.start_span(operation="Middleware")
        request.scout_middleware_span = span
        track_request_queue_time(request, tracked_request)

    def process_response(self, request, response):
        tr = TrackedRequest.instance()
        if (
            hasattr(request, "scout_middleware_span")
            and tr.current_span() == request.scout_middleware_span
        ):
            tr.stop_span()
        return response


class OldStyleViewMiddleware(object):
    def process_request(self, request):
        pass

    def process_view(self, request, view_func, view_func_args, view_func_kwargs):
        tr = TrackedRequest.instance()

        if ignore_path(request.path):
            tr.tag("ignore_transaction", True)

        view_name = request.resolver_match._func_path
        operation = "Controller/" + view_name

        span = tr.start_span(operation=operation)
        tr.mark_real_request()

        # Save the span into the request, so we can check
        # if we're matched up when stopping
        request.scout_view_span = span

        try:
            if getattr(request, "user", None) is not None:
                Context.add("username", request.user.get_username())
        except Exception:
            pass

        return None

    # Process Response could be called without ever having called process_view.
    # Be careful to not stop a span that never got started
    def process_response(self, request, response):
        tr = TrackedRequest.instance()
        if (
            hasattr(request, "scout_view_span")
            and tr.current_span() == request.scout_view_span
        ):
            tr.stop_span()
        return response

    def process_exception(self, request, exception):
        tr = TrackedRequest.instance()

        if (
            hasattr(request, "scout_view_span")
            and tr.current_span() == request.scout_view_span
        ):
            tr.tag("error", "true")
            tr.stop_span()

        return None
