const apiBase = "/";

async function fetchProjects(){
  const res = await fetch(apiBase + "projects");
  return res.json();
}

async function fetchModules(project){
  const res = await fetch(apiBase + "modules/" + encodeURIComponent(project));
  return res.json();
}

async function init(){
  const psel = document.getElementById("project");
  const msel = document.getElementById("module");
  const dateInput = document.getElementById("date");
  const dlBtn = document.getElementById("download");

  const pj = await fetchProjects();
  psel.innerHTML = "";
  pj.projects.forEach(p => {
    const o = document.createElement("option"); o.value = p; o.text = p;
    psel.appendChild(o);
  });

  async function onProjectChange(){
    const p = psel.value;
    msel.innerHTML = "";
    const mods = await fetchModules(p);
    if (mods.modules && mods.modules.length){
      mods.modules.forEach(m => {
        const o = document.createElement("option"); o.value = m; o.text = m;
        msel.appendChild(o);
      });
    } else {
      const o = document.createElement("option"); o.text = "No modules"; msel.appendChild(o);
    }
  }

  psel.addEventListener("change", onProjectChange);
  await onProjectChange();

  dlBtn.addEventListener("click", async ()=>{
    const project = psel.value;
    const module = msel.value;
    const date = dateInput.value.trim();
    let url = `/download?project=${encodeURIComponent(project)}&module=${encodeURIComponent(module)}`;
    if (date) url += `&date=${encodeURIComponent(date)}`;
    window.location = url; // will trigger download
  });
}

init().catch(e => console.error(e));
