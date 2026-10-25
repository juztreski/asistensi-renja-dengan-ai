(function () {
  "use strict";

  function formatBytes(bytes) {
    if (!bytes) return "0 B";
    var units = ["B", "KB", "MB", "GB"];
    var index = Math.floor(Math.log(bytes) / Math.log(1024));
    index = Math.min(index, units.length - 1);
    return (bytes / Math.pow(1024, index)).toFixed(index === 0 ? 0 : 1) + " " + units[index];
  }

  function describeFiles(input) {
    return Array.prototype.map
      .call(input.files || [], function (file) {
        return file.name + " (" + formatBytes(file.size) + ")";
      })
      .join(", ");
  }

  document.querySelectorAll("[data-file-input]").forEach(function (input) {
    var label = input.closest("label");
    var nameElement = label ? label.querySelector("[data-file-name]") : null;

    function update() {
      if (nameElement) {
        nameElement.textContent = input.files.length ? describeFiles(input) : "Belum ada file dipilih";
      }
      if (label) {
        label.classList.toggle("has-file", input.files.length > 0);
      }
    }

    input.addEventListener("change", update);

    if (label) {
      ["dragenter", "dragover"].forEach(function (eventName) {
        label.addEventListener(eventName, function (event) {
          event.preventDefault();
          event.stopPropagation();
          label.classList.add("dragover");
        });
      });
      ["dragleave", "drop"].forEach(function (eventName) {
        label.addEventListener(eventName, function (event) {
          event.preventDefault();
          event.stopPropagation();
          label.classList.remove("dragover");
        });
      });
      label.addEventListener("drop", function (event) {
        if (!event.dataTransfer || !event.dataTransfer.files.length) return;
        try {
          input.files = event.dataTransfer.files;
          update();
        } catch (error) {
          // Some browsers prevent assigning FileList; the click picker remains available.
        }
      });
    }

    update();
  });

  var analyzeForm = document.getElementById("analyze-form");
  if (analyzeForm) {
    analyzeForm.addEventListener("submit", function (event) {
      if (!analyzeForm.checkValidity()) return;
      var button = analyzeForm.querySelector("[data-submit-button]");
      if (!button) return;
      button.disabled = true;
      button.classList.add("is-loading");
      var label = button.querySelector(".btn-label");
      if (label) label.textContent = "Menganalisis dokumen...";
    });
  }

  var filterButtons = Array.prototype.slice.call(document.querySelectorAll("[data-filter-group]"));
  var findingCards = Array.prototype.slice.call(document.querySelectorAll("[data-finding-list] [data-severity]"));
  var visibleCount = document.querySelector("[data-visible-count]");
  var filters = { severity: "all", status: "all" };

  function applyFilters() {
    var visible = 0;
    findingCards.forEach(function (card) {
      var severityMatch = filters.severity === "all" || card.dataset.severity === filters.severity;
      var statusMatch = filters.status === "all" || card.dataset.status === filters.status;
      var show = severityMatch && statusMatch;
      card.classList.toggle("is-hidden", !show);
      if (show) visible += 1;
    });
    if (visibleCount) visibleCount.textContent = String(visible);
  }

  filterButtons.forEach(function (button) {
    button.addEventListener("click", function () {
      var group = button.dataset.filterGroup;
      filters[group] = button.dataset.filterValue;
      filterButtons
        .filter(function (item) { return item.dataset.filterGroup === group; })
        .forEach(function (item) { item.classList.toggle("active", item === button); });
      applyFilters();
    });
  });

  var reviewForm = document.getElementById("review-form");
  if (reviewForm) {
    reviewForm.addEventListener("change", function (event) {
      var select = event.target.closest(".status-select select");
      if (!select) return;
      var card = select.closest("[data-finding-list] [data-status]");
      if (card) {
        card.dataset.status = select.value;
        applyFilters();
      }
    });
  }

  document.querySelectorAll("[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      var message = form.getAttribute("data-confirm");
      if (message && !window.confirm(message)) {
        event.preventDefault();
      }
    });
  });
})();
