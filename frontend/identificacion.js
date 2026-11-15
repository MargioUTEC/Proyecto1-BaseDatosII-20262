(function () {
  "use strict";

  var API = (window.location.origin || "") + "/api";

  function $(id) { return document.getElementById(id); }

  function ms(valor) {
    if (valor === null || valor === undefined) return "—";
    return (valor < 10 ? valor.toFixed(2) : Math.round(valor)) + " ms";
  }

  function miles(valor) {
    if (valor === null || valor === undefined) return "—";
    return String(valor).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  }

  /* ---------- estado de los servicios ---------- */

  function comprobar() {
    fetch(API + "/identificacion/estado")
      .then(function (r) { return r.json(); })
      .then(function (datos) {
        var punto = $("punto");
        if (datos.disponible) {
          punto.className = "punto vivo";
          $("estadoTexto").textContent =
            datos.images + " rostros indexados en el bloque vectorial";
        } else {
          punto.className = "punto caido";
          $("estadoTexto").textContent = "el bloque vectorial no responde";
          $("aviso").textContent = datos.detalle || "";
        }
      })
      .catch(function () {
        $("punto").className = "punto caido";
        $("estadoTexto").textContent = "el motor no responde";
      });
  }

  /* ---------- la foto ---------- */

  var elegida = null;

  function tomar(archivo) {
    if (!archivo || !/^image\//.test(archivo.type)) {
      $("aviso").textContent = "eso no parece una imagen";
      return;
    }
    elegida = archivo;
    var lector = new FileReader();
    lector.onload = function (evento) {
      var vista = $("vistaPrevia");
      vista.src = evento.target.result;
      vista.hidden = false;
      $("zonaTexto").hidden = true;
    };
    lector.readAsDataURL(archivo);
    $("buscar").disabled = false;
    $("aviso").textContent = archivo.name;
  }

  $("archivo").addEventListener("change", function (evento) {
    tomar(evento.target.files[0]);
  });

  ["dragenter", "dragover"].forEach(function (nombre) {
    $("zona").addEventListener(nombre, function (evento) {
      evento.preventDefault();
      $("zona").classList.add("encima");
    });
  });

  ["dragleave", "drop"].forEach(function (nombre) {
    $("zona").addEventListener(nombre, function (evento) {
      evento.preventDefault();
      $("zona").classList.remove("encima");
    });
  });

  $("zona").addEventListener("drop", function (evento) {
    tomar(evento.dataTransfer.files[0]);
  });

  /* ---------- la consulta ---------- */

  $("buscar").addEventListener("click", function () {
    if (!elegida) return;
    var formulario = new FormData();
    formulario.append("file", elegida);
    formulario.append("k", $("k").value || "5");
    formulario.append("proyeccion", "id,lote,embedding");
    formulario.append("filtro", $("filtro").value || "");

    $("buscar").disabled = true;
    $("buscar").textContent = "identificando…";
    $("aviso").textContent = "";

    fetch(API + "/identificacion/buscar", { method: "POST", body: formulario })
      .then(function (respuesta) {
        return respuesta.json().then(function (datos) {
          if (!respuesta.ok) throw new Error(datos.detail || datos.error || "fallo la consulta");
          return datos;
        });
      })
      .then(pintar)
      .catch(function (error) {
        $("aviso").textContent = error.message;
        $("rejilla").innerHTML = '<p class="vacio-nota">' + escapar(error.message) + "</p>";
      })
      .finally(function () {
        $("buscar").disabled = false;
        $("buscar").textContent = "Identificar";
      });
  });

  /* ---------- el resultado ---------- */

  function pintar(datos) {
    var columnas = datos.columns || [];
    var posRuta = columnas.indexOf("embedding");
    var posId = columnas.indexOf("id");
    var posSim = columnas.indexOf("similitud");

    var rejilla = $("rejilla");
    rejilla.innerHTML = "";
    if (!datos.rows.length) {
      rejilla.innerHTML = '<p class="vacio-nota">Ninguna cara pasó el filtro.</p>';
    }
    datos.rows.forEach(function (fila, orden) {
      var ruta = posRuta >= 0 ? fila[posRuta] : "";
      var similitud = posSim >= 0 ? fila[posSim] : 0;
      var tarjeta = document.createElement("figure");
      tarjeta.className = "cara" + (orden === 0 ? " primera" : "");
      tarjeta.innerHTML =
        '<img loading="lazy" alt="rostro ' + escapar(String(fila[posId])) + '" src="' +
        API + "/identificacion/imagen?path=" + encodeURIComponent(ruta) + '" />' +
        '<figcaption><span class="id">#' + escapar(String(fila[posId])) + "</span>" +
        '<span class="sim">' + (similitud * 100).toFixed(1) + "%</span></figcaption>";
      rejilla.appendChild(tarjeta);
    });

    var sello = $("estrategia");
    sello.hidden = !datos.estrategia;
    sello.textContent = datos.estrategia || "";

    $("sql").textContent = acortarVector(datos.sql || "—");

    var m = datos.metrics || {};
    $("tVector").textContent = ms(datos.embedding_ms);
    $("tPlan").textContent = ms(m.plan_ms);
    $("tEjec").textContent = ms(m.execution_ms);
    $("tTotal").textContent = ms((datos.embedding_ms || 0) + (m.total_ms || 0));

    var vectorial = nodoVectorial(datos.plan);
    var runtime = (vectorial && vectorial.runtime) || {};
    $("bIndice").textContent = miles((runtime.index_reads || 0) + (runtime.index_writes || 0));
    $("bTabla").textContent = miles((runtime.table_reads || 0) + (runtime.table_writes || 0));
    $("bCand").textContent = miles(runtime.candidates);
    $("plan").textContent = datos.plan_text || "—";
  }

  function nodoVectorial(nodo) {
    if (!nodo) return null;
    if (nodo.node === "IVFScan" || nodo.node === "IVFRefine") return nodo;
    var hijos = nodo.children || [];
    for (var i = 0; i < hijos.length; i += 1) {
      var hallado = nodoVectorial(hijos[i]);
      if (hallado) return hallado;
    }
    return null;
  }

  function acortarVector(sql) {
    return sql.replace(/'\[[^\]]{60,}\]'/, function (trozo) {
      return "'[" + trozo.slice(2, 40) + " … ]'";
    });
  }

  function escapar(texto) {
    return String(texto).replace(/[&<>"']/g, function (caracter) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[caracter];
    });
  }

  comprobar();
})();
