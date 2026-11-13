(function(){
  var x=document.getElementById('notice-x'),n=document.getElementById('notice');
  if(x&&n)x.addEventListener('click',function(){n.remove();});
  var b=document.getElementById('user-btn'),m=document.getElementById('user-menu');
  if(b&&m){
    b.addEventListener('click',function(e){e.stopPropagation();m.hidden=!m.hidden;});
    document.addEventListener('click',function(e){if(!m.contains(e.target))m.hidden=true;});
  }
})();
