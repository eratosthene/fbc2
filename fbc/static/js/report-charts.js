// Draws the charts on the Reports. Each <canvas class="report-chart"> has its
// data in a <script type="application/json"> beside it, whose id is the
// canvas's id plus "-data": {labels, unit, series: [{label, kind, data}]}.
// kind is "bar", "line", or "baseline" (a flat dashed line to read against).
// Colours are the data-viz palette's first slots, in order.
(function () {
  var COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"];
  var GREY = "#6b6a66";

  function money(n) {
    var s = Math.abs(n).toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2});
    return (n < 0 ? "-$" : "$") + s;
  }
  function format(unit, n) {
    if (n === null || n === undefined) return "";
    if (unit === "$") return money(n);
    if (unit === "%") return n + "%";
    return String(n);
  }

  document.querySelectorAll("canvas.report-chart").forEach(function (canvas) {
    var holder = document.getElementById(canvas.id + "-data");
    if (!holder || typeof Chart === "undefined") return;
    var spec = JSON.parse(holder.textContent);
    var color = 0;
    var datasets = spec.series.map(function (s) {
      if (s.kind === "baseline") {
        return {type: "line", label: s.label, data: s.data, borderColor: GREY,
                borderDash: [6, 4], borderWidth: 1.5, pointRadius: 0, pointHitRadius: 0, order: 3};
      }
      var c = COLORS[color++ % COLORS.length];
      if (s.kind === "bar") {
        return {type: "bar", label: s.label, data: s.data, backgroundColor: c,
                borderRadius: 3, maxBarThickness: 28, order: 2};
      }
      return {type: "line", label: s.label, data: s.data, borderColor: c, backgroundColor: c,
              borderWidth: 2, pointRadius: 3, pointBackgroundColor: c, pointBorderColor: "#fff",
              pointBorderWidth: 1, tension: 0, spanGaps: false, order: 1};
    });
    new Chart(canvas, {
      data: {labels: spec.labels, datasets: datasets},
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: {mode: "index", intersect: false},
        plugins: {
          legend: {position: "bottom", labels: {usePointStyle: true, boxWidth: 8}},
          tooltip: {
            filter: function (item) { return item.raw !== null; },
            callbacks: {label: function (ctx) { return ctx.dataset.label + ": " + format(spec.unit, ctx.parsed.y); }}
          }
        },
        scales: {
          x: {grid: {display: false}},
          y: {grid: {color: "#e6e5e1"}, ticks: {callback: function (v) { return format(spec.unit, v); }}}
        }
      }
    });
  });
})();
