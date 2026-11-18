/* Polls an AI job's status URL and reports each state. Same-origin only; no cookies or tokens are stored.
 * The status URL is kept by the page (server-rendered or in the address), so a refresh re-attaches to the
 * stored job. Output is written with textContent only. */
(function () {
  "use strict";
  var FINAL = { SUCCEEDED: true, FAILED: true };

  function pollJob(statusUrl, options) {
    var opts = options || {};
    var intervalMs = opts.intervalMs || 1500;
    var stopped = false;

    function tick() {
      if (stopped) return;
      fetch(statusUrl, { headers: { Accept: "application/json" }, credentials: "same-origin" })
        .then(function (r) {
          if (!r.ok) throw new Error("status " + r.status);
          return r.json();
        })
        .then(function (job) {
          if (opts.onUpdate) opts.onUpdate(job);
          if (!FINAL[job.state]) window.setTimeout(tick, intervalMs);
        })
        .catch(function (err) {
          if (opts.onError) opts.onError(err);
          if (!stopped) window.setTimeout(tick, intervalMs * 2);
        });
    }

    tick();
    return function stop() { stopped = true; };
  }

  function renderJobState(element, job) {
    var labels = { PENDING: "대기 중", RUNNING: "진행 중", SUCCEEDED: "완료", FAILED: "실패" };
    element.textContent = labels[job.state] || job.state;
    element.setAttribute("data-job-state", job.state);
  }

  window.MoimJobs = { pollJob: pollJob, renderJobState: renderJobState };
})();
