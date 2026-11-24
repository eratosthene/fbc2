// The Columns button on a list: move a column up or down, saved in this
// browser for this list (localStorage), so a phone and a desktop keep their
// own orders. Cells with no data-col (checkbox, show/edit/delete) stay left.
// A column the saved order doesn't know shows at the end; Reset forgets it.
(function () {
  function load(key) {
    try {
      var v = JSON.parse(window.localStorage.getItem(key));
      return Array.isArray(v) ? v : [];
    } catch (e) {
      return [];
    }
  }

  function save(key, order) {
    try {
      if (order) window.localStorage.setItem(key, JSON.stringify(order));
      else window.localStorage.removeItem(key);
    } catch (e) {
      // Storage is blocked: the order holds until the page reloads.
    }
  }

  function setup(box) {
    var well = box.closest(".well");
    var wrap = well && well.nextElementSibling;
    var table = wrap && wrap.querySelector("table");
    if (!table) {
      box.style.display = "none"; // "No records found": nothing to move.
      return;
    }
    var key = "fbc-columns:" + box.getAttribute("data-key");
    var headRow = table.tHead.rows[0];
    var heads = {};
    var labels = {};
    var natural = [];
    Array.prototype.forEach.call(headRow.children, function (th) {
      var name = th.getAttribute("data-col");
      if (!name) return;
      natural.push(name);
      heads[name] = th;
      labels[name] = th.textContent.replace(/\s+/g, " ").trim() || name;
    });

    var order = natural.slice();

    function apply(names) {
      var rows = Array.prototype.slice.call(table.rows);
      rows.forEach(function (row) {
        var cells = {};
        Array.prototype.forEach.call(row.children, function (c) {
          var n = c.getAttribute("data-col");
          if (n) cells[n] = c;
        });
        // The movable cells are the last ones in the row, so appending in
        // order puts them back in that order after the fixed ones.
        names.forEach(function (n) {
          if (cells[n]) row.appendChild(cells[n]);
        });
      });
    }

    function sorted(saved) {
      var known = saved.filter(function (n) {
        return natural.indexOf(n) !== -1;
      });
      var rest = natural.filter(function (n) {
        return known.indexOf(n) === -1;
      });
      return known.concat(rest);
    }

    var panel = document.createElement("div");
    panel.className = "fbc-columns-panel";
    panel.style.display = "none";
    box.appendChild(panel);

    function draw() {
      panel.innerHTML = "";
      var ul = document.createElement("ul");
      ul.className = "list-unstyled";
      order.forEach(function (name, i) {
        var li = document.createElement("li");
        function btn(text, label, disabled, to) {
          var b = document.createElement("button");
          b.type = "button";
          b.className = "btn btn-xs btn-default";
          b.textContent = text;
          b.setAttribute("aria-label", label + " " + labels[name]);
          b.disabled = disabled;
          b.addEventListener("click", function () {
            move(i, to);
          });
          return b;
        }
        li.appendChild(btn("↑", "Move up", i === 0, i - 1));
        li.appendChild(document.createTextNode(" "));
        li.appendChild(btn("↓", "Move down", i === order.length - 1, i + 1));
        li.appendChild(document.createTextNode(" " + labels[name]));
        ul.appendChild(li);
      });
      panel.appendChild(ul);
      var reset = document.createElement("button");
      reset.type = "button";
      reset.className = "btn btn-xs btn-default fbc-columns-reset";
      reset.textContent = "Reset";
      reset.addEventListener("click", function () {
        order = natural.slice();
        save(key, null);
        apply(order);
        draw();
      });
      panel.appendChild(reset);
    }

    function move(from, to) {
      var name = order.splice(from, 1)[0];
      order.splice(to, 0, name);
      save(key, order);
      apply(order);
      draw();
    }

    box
      .querySelector(".fbc-columns-toggle")
      .addEventListener("click", function () {
        var open = panel.style.display === "none";
        if (open) draw();
        panel.style.display = open ? "block" : "none";
      });

    order = sorted(load(key));
    apply(order);
  }

  Array.prototype.forEach.call(
    document.querySelectorAll(".fbc-columns"),
    function (box) {
      if (box.getAttribute("data-ready")) return;
      box.setAttribute("data-ready", "1");
      setup(box);
    }
  );
})();
