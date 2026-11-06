"""Jobs and their event streams. Every event is kept, so a late subscriber still sees the whole job."""
import itertools
import threading
import time
import uuid


class Job:
    def __init__(self, kind):
        self.id = uuid.uuid4().hex[:10]
        self.kind = kind
        self.events = []
        self.cond = threading.Condition()
        self.finished = False
        self._seq = itertools.count()

    def emit(self, type_, **payload):
        ev = {"job": self.id, "seq": next(self._seq), "ts": round(time.time(), 3), "type": type_, **payload}
        with self.cond:
            self.events.append(ev)
            if type_ == "done":
                self.finished = True
            self.cond.notify_all()

    def stream(self, timeout=15.0):
        """Yields every event from the start; yields None as a keep-alive while waiting."""
        i = 0
        while True:
            with self.cond:
                if i >= len(self.events) and not self.finished:
                    self.cond.wait(timeout)
                batch = self.events[i:]
                done = self.finished
            if not batch:
                if done:
                    return
                yield None
                continue
            for ev in batch:
                yield ev
            i += len(batch)
            if done and i >= len(self.events):
                return


JOBS = {}


def start(kind, fn, *args):
    """Runs fn(job, *args) in a thread; any exception becomes an error event. Always ends with done."""
    job = Job(kind)
    JOBS[job.id] = job

    def go():
        try:
            fn(job, *args)
        except Exception as e:   # surface every failure in the stream instead of a silent thread death
            job.emit("error", msg=f"{type(e).__name__}: {e}")
        finally:
            job.emit("done")
    threading.Thread(target=go, daemon=True).start()
    return job
