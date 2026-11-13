(function(){
  // dropdown menus (positioned under their button so they are not clipped by the scrolling bar)
  var open=null;
  function close(){if(open){open.classList.remove('open');open=null;}}
  document.querySelectorAll('.dd>button').forEach(function(b){
    b.addEventListener('click',function(e){e.stopPropagation();var dd=b.parentNode;
      if(open===dd){close();return;}close();dd.classList.add('open');open=dd;
      var m=dd.querySelector('.dd-menu'),r=b.getBoundingClientRect();m.style.top=r.bottom+'px';m.style.left=Math.min(r.left,window.innerWidth-220)+'px';});
  });
  document.addEventListener('click',close);
  // clickable table rows
  document.querySelectorAll('tr.click').forEach(function(tr){tr.addEventListener('click',function(){location.href=tr.dataset.href;});});
})();
