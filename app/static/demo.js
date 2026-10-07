// One orchestrated moment: junk characters collapse out of each sender name, row by row.
(function () {
  var demo = document.querySelector(".demo");
  if (!demo) return;
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduce) { demo.classList.add("resolved", "instant"); return; }
  demo.classList.add("armed");
  var start = function () { requestAnimationFrame(function () { demo.classList.add("resolved"); }); };
  if ("IntersectionObserver" in window) {
    var io = new IntersectionObserver(function (entries) {
      if (entries[0].isIntersecting) { io.disconnect(); setTimeout(start, 500); }
    }, { threshold: 0.4 });
    io.observe(demo);
  } else {
    setTimeout(start, 500);
  }
})();
