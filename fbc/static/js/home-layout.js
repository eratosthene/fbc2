// Customize mode on the home page (#214): drag a card to move it, Hide and
// Show, Reset to default. Each change is saved as it is made. Sortable.min.js
// is loaded before this; nothing here uses the network but fbc's own routes.
(function () {
  var list = document.getElementById("home-cards");
  if (!list) return;
  var csrf = list.getAttribute("data-csrf");

  function post(url, body) {
    var headers = { "Content-Type": "application/json" };
    if (csrf) headers["X-CSRFToken"] = csrf;
    return fetch(url, {
      method: "POST",
      headers: headers,
      credentials: "same-origin",
      body: JSON.stringify(body || {}),
    }).then(function (r) {
      if (!r.ok) throw new Error("Not saved (" + r.status + ")");
    });
  }

  function failed(e) {
    alert(e.message + ". Reload the page and try again.");
  }

  // Hide, Show and Reset change which cards the page works out, so reload.
  function reloading(button, body) {
    button.addEventListener("click", function () {
      if (body && !window.confirm(body)) return;
      button.disabled = true;
      post(button.getAttribute("data-url"))
        .then(function () {
          window.location.reload();
        })
        .catch(function (e) {
          button.disabled = false;
          failed(e);
        });
    });
  }

  Array.prototype.forEach.call(
    document.querySelectorAll(".home-hide, .home-show"),
    function (b) {
      reloading(b);
    }
  );
  var reset = document.getElementById("home-reset");
  if (reset) reloading(reset, reset.getAttribute("data-confirm"));

  new Sortable(list, {
    handle: ".home-handle",
    draggable: ".home-card",
    animation: 150,
    onEnd: function () {
      var order = Array.prototype.map.call(
        list.querySelectorAll(".home-card"),
        function (card) {
          return card.getAttribute("data-key");
        }
      );
      post(list.getAttribute("data-order-url"), { order: order }).catch(failed);
    },
  });
})();
