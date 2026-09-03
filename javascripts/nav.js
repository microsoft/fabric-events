document.addEventListener("DOMContentLoaded", function () {
  const tabsList = document.querySelector(".md-tabs__list");
  const headerInner = document.querySelector(".md-header__inner");
  const tabsBar = document.querySelector(".md-tabs");

  if (tabsList && headerInner) {
    headerInner.appendChild(tabsList);
    if (tabsBar) tabsBar.style.display = "none";
  }
});
