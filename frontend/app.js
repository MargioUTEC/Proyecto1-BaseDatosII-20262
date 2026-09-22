/* Consola SQL — CS2042
 *
 * Cliente del motor de consultas. Cuatro paneles: catalogo, editor, resultados
 * y telemetria. El resaltado no usa un modo SQL generico: reconoce exactamente
 * las palabras que este motor entiende, asi que una sentencia que el parser
 * rechazaria se ve apagada mientras se escribe.
 */
(function () {
  "use strict";

  var API = (window.location.origin || "") + "/api";
  var FILAS_POR_PAGINA = 100;

  // Mismo vocabulario que queryengine/sql/tokens.py
  var CLAVES = ("AND AS ASC BETWEEN BY COPY CREATE DELETE DELIMITER DESC DROP EXPLAIN FALSE FROM " +
    "HEADER INDEX INSERT INTO IS KEY LIMIT NOT NULL ON OR ORDER PRIMARY SELECT TABLE TRUE " +
    "USING VALUES WHERE WITH").split(" ");
  var TIPOS = ("INT BIGINT FLOAT DOUBLE BOOL CHAR VARCHAR DATE HEAP SEQUENTIAL BTREE HASH " +
    "PAGE_SIZE").split(" ");

  var RECETAS = [
    ["Crear la tabla de clientes", [
      'CREATE TABLE customers (',
      '  "Index" INT PRIMARY KEY, "Customer Id" CHAR(15), "First Name" CHAR(16),',
      '  "Last Name" CHAR(16), "Company" CHAR(40), "City" CHAR(28), "Country" CHAR(56),',
      '  "Phone 1" CHAR(24), "Phone 2" CHAR(24), "Email" CHAR(48),',
      '  "Subscription Date" DATE, "Website" CHAR(44)',
      ') USING HEAP;'
    ].join("\n")],
    ["Cargar el CSV", "COPY customers FROM 'customers-100000.csv';"],
    ["Indice B+ sobre la clave", 'CREATE INDEX ix_btree ON customers("Index") USING BTREE;'],
    ["Indice hash sobre la clave", 'CREATE INDEX ix_hash ON customers("Index") USING HASH;'],
    ["Busqueda puntual", 'SELECT "First Name", "Last Name", "Country"\n  FROM customers\n WHERE "Index" = 77777;'],
    ["Busqueda por rango", 'SELECT "Index", "City", "Country"\n  FROM customers\n WHERE "Index" BETWEEN 5000 AND 5020;'],
    ["Rango amplio (vuelve a SeqScan)", 'SELECT "Index" FROM customers WHERE "Index" >= 1 AND "Index" <= 60000;'],
    ["Ver el plan sin ejecutar", 'EXPLAIN SELECT * FROM customers WHERE "Index" = 42;'],
    ["Tabla ordenada con overflow", [
      'CREATE TABLE clientes_ord (',
      '  "Index" INT PRIMARY KEY, "Customer Id" CHAR(15), "First Name" CHAR(16),',
      '  "Last Name" CHAR(16), "Company" CHAR(40), "City" CHAR(28), "Country" CHAR(56),',
      '  "Phone 1" CHAR(24), "Phone 2" CHAR(24), "Email" CHAR(48),',
      '  "Subscription Date" DATE, "Website" CHAR(44)',
      ') USING SEQUENTIAL;'
    ].join("\n")],
    ["Paginas de 8 KB", 'CREATE TABLE grande ("Index" INT PRIMARY KEY, "Country" CHAR(56))\n  WITH (PAGE_SIZE = 8192);'],
    ["Borrar una fila", 'DELETE FROM customers WHERE "Index" = 77777;']
  ];

  var $ = function (id) { return document.getElementById(id); };
  var sql = $("sql"), pintado = $("pintado");
  var ultimo = { columnas: [], filas: [], pagina: 0 };

  /* ---------- resaltado ---------- */

  function escapar(texto) {
    return texto.replace(/[&<>]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c];
    });
  }

  function pintar(texto) {
    var patron = /(--[^\n]*|\/\*[\s\S]*?\*\/)|('(?:[^']|'')*')|("(?:[^"])*")|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][A-Za-z0-9_$]*)/g;
    var salida = "", ultimoIndice = 0, coincidencia;
    while ((coincidencia = patron.exec(texto)) !== null) {
      salida += escapar(texto.slice(ultimoIndice, coincidencia.index));
      var bruto = coincidencia[0], clase;
      if (coincidencia[1]) clase = "tk-com";
      else if (coincidencia[2]) clase = "tk-str";
      else if (coincidencia[3]) clase = "tk-id";
      else if (coincidencia[4]) clase = "tk-num";
      else {
        var mayus = bruto.toUpperCase();
        clase = CLAVES.indexOf(mayus) >= 0 ? "tk-kw"
              : TIPOS.indexOf(mayus) >= 0 ? "tk-tipo" : "tk-id";
      }
      salida += '<span class="' + clase + '">' + escapar(bruto) + "</span>";
      ultimoIndice = coincidencia.index + bruto.length;
    }
    salida += escapar(texto.slice(ultimoIndice));
    return salida + "\n";
  }

  function repintar() {
    pintado.innerHTML = pintar(sql.value);
    pintado.scrollTop = sql.scrollTop;
    pintado.scrollLeft = sql.scrollLeft;
  }

  /* ---------- red ---------- */

  function pedir(ruta, cuerpo) {
    var opciones = cuerpo
      ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(cuerpo) }
      : {};
    return fetch(API + ruta, opciones).then(function (respuesta) {
      return respuesta.json().then(function (datos) {
        if (!respuesta.ok) throw datos;
        return datos;
      });
    });
  }

  function marcarEnlace(estado, texto) {
    $("enlace").dataset.estado = estado;
    $("enlaceTexto").textContent = texto;
  }

  /* ---------- catalogo ---------- */

  function cargarTablas() {
    return pedir("/tables").then(function (datos) {
      marcarEnlace("ok", "motor en linea");
      dibujarTablas(datos.tables || []);
    }).catch(function () {
      marcarEnlace("error", "sin motor");
      $("listaTablas").innerHTML =
        '<p class="vacio-nota">No se pudo hablar con el motor. Revisa que este levantado.</p>';
    });
  }

  function dibujarTablas(tablas) {
    var caja = $("listaTablas");
    if (!tablas.length) {
      caja.innerHTML = '<p class="vacio-nota">Todavia no hay tablas. Usa un ejemplo para crear la primera.</p>';
      return;
    }
    caja.innerHTML = "";
    tablas.forEach(function (tabla) {
      var ficha = document.createElement("div");
      ficha.className = "tabla-ficha";

      var jefe = document.createElement("button");
      jefe.className = "tabla-jefe";
      jefe.type = "button";
      jefe.title = "Escribir un SELECT sobre " + tabla.name;
      jefe.innerHTML =
        '<span class="tabla-nombre">' + escapar(tabla.name) + "</span>" +
        '<span class="tabla-motor">' + escapar(tabla.storage) + "</span>";
      jefe.addEventListener("click", function () {
        sql.value = 'SELECT * FROM ' + tabla.name + " LIMIT 50;";
        repintar();
        sql.focus();
      });
      ficha.appendChild(jefe);

      var cifras = document.createElement("div");
      cifras.className = "tabla-cifras";
      cifras.textContent =
        miles(tabla.row_count) + " filas · " + miles(tabla.page_count) + " pag · " +
        tabla.stored_record_size + " B/reg · " + tabla.records_per_page + "/pag";
      cifras.title = "Formato del registro: " + tabla.record_format +
        " · pagina de " + tabla.page_size + " bytes";
      ficha.appendChild(cifras);

      if (tabla.storage === "SEQUENTIAL") {
        var reorg = document.createElement("button");
        reorg.className = "reorganizar";
        reorg.type = "button";
        reorg.textContent = "reorganizar";
        reorg.title = "Fusiona el area de desbordamiento con la principal";
        reorg.addEventListener("click", function () {
          reorg.disabled = true;
          reorg.textContent = "reorganizando…";
          pedir("/tables/reorganize", { table: tabla.name }).then(function (datos) {
            var informe = datos.report, metricas = datos.metrics;
            $("pieEstado").textContent =
              informe.records_kept + " registros reescritos, " +
              informe.records_from_overflow + " rescatados del overflow · " +
              informe.pages_before + " → " + informe.pages_after + " paginas";
            $("pieCosto").textContent =
              metricas.disk_reads + " R · " + metricas.disk_writes + " W · " +
              ms(metricas.execution_ms);
            cargarTablas();
          }).catch(function (error) {
            mostrarError(error);
            reorg.disabled = false;
            reorg.textContent = "reorganizar";
          });
        });
        ficha.appendChild(reorg);
      }

      var columnas = document.createElement("ul");
      columnas.className = "col-lista";
      tabla.columns.forEach(function (columna) {
        var fila = document.createElement("li");
        fila.innerHTML =
          '<span class="' + (columna.primary_key ? "pk" : "") + '">' +
            escapar(columna.name) + (columna.primary_key ? " ◆" : "") + "</span>" +
          '<span class="col-tipo">' + escapar(columna.type) + "</span>";
        columnas.appendChild(fila);
      });
      ficha.appendChild(columnas);

      if (tabla.indexes && tabla.indexes.length) {
        var indices = document.createElement("ul");
        indices.className = "ix-lista";
        tabla.indexes.forEach(function (indice) {
          var donde = indice.backend || "";
          var item = document.createElement("li");
          item.className = "ix" + (donde.indexOf("disco") >= 0 ? " en-disco" : "");
          item.innerHTML =
            "<b>" + escapar(indice.name) + "</b> " + escapar(indice.kind) +
            " (" + escapar(indice.column) + ")" +
            '<span class="donde">' + escapar(donde) + "</span>";
          indices.appendChild(item);
        });
        ficha.appendChild(indices);
      }
      caja.appendChild(ficha);
    });
  }

  /* ---------- ejecucion ---------- */

  function ejecutar() {
    var texto = sql.value.trim();
    if (!texto) return;
    $("ejecutar").disabled = true;
    $("aviso").hidden = true;
    $("pieEstado").textContent = "Ejecutando…";

    pedir("/query", { sql: texto }).then(function (datos) {
      mostrarResultado(datos);
      mostrarTelemetria(datos);
      if (/^(CreateTable|DropTable|CreateIndex|DropIndex|Copy|Insert|Delete)$/.test(datos.statement)) {
        cargarTablas();
      }
    }).catch(function (error) {
      mostrarError(error);
    }).then(function () {
      $("ejecutar").disabled = false;
    });
  }

  function mostrarError(error) {
    var aviso = $("aviso");
    // El motor ya incluye la posicion dentro del mensaje, no se repite aqui.
    aviso.textContent = (error && error.error) || "No se pudo contactar al motor.";
    aviso.hidden = false;
    $("pieEstado").textContent = (error && error.kind) || "Error";
    $("pieCosto").textContent = "";
    if (error && error.line) situarCursor(error.line, error.column);
  }

  function situarCursor(linea, columna) {
    var lineas = sql.value.split("\n");
    var posicion = 0;
    for (var i = 0; i < linea - 1 && i < lineas.length; i++) posicion += lineas[i].length + 1;
    posicion += Math.max(0, (columna || 1) - 1);
    sql.focus();
    sql.setSelectionRange(posicion, posicion);
  }

  function mostrarResultado(datos) {
    ultimo.columnas = datos.columns || [];
    ultimo.filas = datos.rows || [];
    ultimo.pagina = 0;

    if (!ultimo.columnas.length) {
      $("salida").innerHTML = '<p class="mensaje">' + escapar(datos.message || "Listo.") + "</p>";
      $("paginador").hidden = true;
      $("pieEstado").textContent = datos.message || "Listo.";
      return;
    }
    dibujarPagina();
    $("pieEstado").textContent = miles(ultimo.filas.length) + " fila(s) recuperada(s).";
  }

  function dibujarPagina() {
    var inicio = ultimo.pagina * FILAS_POR_PAGINA;
    var trozo = ultimo.filas.slice(inicio, inicio + FILAS_POR_PAGINA);

    var html = '<div class="rejilla-envoltura"><table class="rejilla"><thead><tr>';
    html += '<th class="num-fila">#</th>';
    ultimo.columnas.forEach(function (nombre) { html += "<th>" + escapar(nombre) + "</th>"; });
    html += "</tr></thead><tbody>";
    trozo.forEach(function (fila, indice) {
      html += '<tr><td class="num-fila">' + miles(inicio + indice + 1) + "</td>";
      fila.forEach(function (valor) {
        html += valor === null
          ? '<td class="nulo">NULL</td>'
          : "<td>" + escapar(String(valor)) + "</td>";
      });
      html += "</tr>";
    });
    html += "</tbody></table></div>";
    $("salida").innerHTML = html;

    var paginas = Math.max(1, Math.ceil(ultimo.filas.length / FILAS_POR_PAGINA));
    $("paginador").hidden = paginas <= 1;
    $("pagina").textContent = (ultimo.pagina + 1) + " / " + paginas;
    $("prev").disabled = ultimo.pagina === 0;
    $("sig").disabled = ultimo.pagina >= paginas - 1;
  }

  /* ---------- telemetria ---------- */

  function mostrarTelemetria(datos) {
    var metricas = datos.metrics || {};
    $("mReads").textContent = miles(metricas.disk_reads);
    $("mWrites").textContent = miles(metricas.disk_writes);
    $("tParse").textContent = ms(metricas.parse_ms);
    $("tPlan").textContent = ms(metricas.plan_ms);
    $("tExec").textContent = ms(metricas.execution_ms);
    $("tTotal").textContent = ms(metricas.total_ms);

    var estimado = costoDe(datos.plan);
    var medido = metricas.disk_reads + metricas.disk_writes;
    var caja = $("contraste");
    if (estimado === null) {
      caja.hidden = true;
    } else {
      caja.hidden = false;
      var tope = Math.max(estimado, medido, 1);
      $("barraEst").style.width = (estimado / tope * 100) + "%";
      $("barraMed").style.width = (medido / tope * 100) + "%";
      $("cifraEst").textContent = miles(Math.round(estimado));
      $("cifraMed").textContent = miles(medido);

      var desvio = $("desvio");
      var brecha = medido - estimado;
      if (Math.abs(brecha) < 0.5) {
        desvio.textContent = "clavado";
        desvio.className = "desvio clavado";
      } else {
        var porcentaje = estimado > 0 ? Math.round(brecha / estimado * 100) : 0;
        desvio.textContent = (brecha > 0 ? "+" : "") + miles(brecha) +
          (estimado > 0 ? " (" + (porcentaje > 0 ? "+" : "") + porcentaje + "%)" : "");
        desvio.className = "desvio";
      }
    }

    $("plan").innerHTML = datos.plan ? dibujarNodo(datos.plan) :
      '<p class="vacio-nota">Sin plan.</p>';
    $("pieCosto").textContent =
      metricas.disk_reads + " R · " + metricas.disk_writes + " W · " + ms(metricas.total_ms);
  }

  var ACCESO = /^(SeqScan|IndexScan|IndexRangeScan|SequentialSearch|SequentialRangeScan)$/;

  function dibujarNodo(nodo) {
    var esAcceso = ACCESO.test(nodo.node);
    var html = '<div class="nodo' + (esAcceso ? " acceso" : "") + '">';
    html += '<div class="nodo-jefe"><span class="nodo-tipo">' + escapar(nodo.node) + "</span>";
    if (nodo.cost) {
      html += '<span class="nodo-costo">~' + nodo.cost.estimated_blocks + " bloques · ~" +
        miles(nodo.cost.estimated_rows) + " filas</span>";
    }
    html += "</div>";

    var detalles = [];
    ["table", "index", "using", "condition", "columns", "by", "rows", "target", "on", "from", "storage"]
      .forEach(function (llave) {
        if (nodo[llave] !== undefined) detalles.push(llave + ": " + nodo[llave]);
      });
    if (detalles.length) {
      html += '<div class="nodo-detalle">' + escapar(detalles.join("  ·  ")) + "</div>";
    }
    if (nodo.cost && nodo.cost.rationale) {
      html += '<div class="nodo-detalle" style="color:var(--tinta-3)">' +
        escapar(nodo.cost.rationale) + "</div>";
    }
    (nodo.children || []).forEach(function (hijo) { html += dibujarNodo(hijo); });
    return html + "</div>";
  }

  function costoDe(nodo) {
    if (!nodo) return null;
    if (nodo.cost) return nodo.cost.estimated_blocks;
    var hijos = nodo.children || [];
    for (var i = 0; i < hijos.length; i++) {
      var encontrado = costoDe(hijos[i]);
      if (encontrado !== null) return encontrado;
    }
    return null;
  }

  /* ---------- utilidades ---------- */

  function miles(numero) {
    if (numero === undefined || numero === null) return "—";
    return Number(numero).toLocaleString("es-PE");
  }
  function ms(valor) {
    if (valor === undefined || valor === null) return "—";
    return (valor < 1 ? valor.toFixed(3) : valor.toFixed(2)) + " ms";
  }

  /* ---------- arranque ---------- */

  RECETAS.forEach(function (receta, indice) {
    var opcion = document.createElement("option");
    opcion.value = String(indice);
    opcion.textContent = receta[0];
    $("recetas").appendChild(opcion);
  });
  $("recetas").addEventListener("change", function (evento) {
    var receta = RECETAS[Number(evento.target.value)];
    if (receta) { sql.value = receta[1]; repintar(); sql.focus(); }
    evento.target.value = "";
  });

  sql.addEventListener("input", repintar);
  sql.addEventListener("scroll", function () {
    pintado.scrollTop = sql.scrollTop;
    pintado.scrollLeft = sql.scrollLeft;
  });
  sql.addEventListener("keydown", function (evento) {
    if ((evento.metaKey || evento.ctrlKey) && evento.key === "Enter") {
      evento.preventDefault();
      ejecutar();
    }
  });

  $("ejecutar").addEventListener("click", ejecutar);
  $("recargar").addEventListener("click", cargarTablas);
  $("prev").addEventListener("click", function () {
    if (ultimo.pagina > 0) { ultimo.pagina--; dibujarPagina(); }
  });
  $("sig").addEventListener("click", function () {
    if ((ultimo.pagina + 1) * FILAS_POR_PAGINA < ultimo.filas.length) {
      ultimo.pagina++; dibujarPagina();
    }
  });

  $("tema").addEventListener("click", function () {
    var actual = document.documentElement.dataset.tema;
    var oscuroAhora = actual
      ? actual === "oscuro"
      : window.matchMedia("(prefers-color-scheme: dark)").matches;
    var siguiente = oscuroAhora ? "claro" : "oscuro";
    document.documentElement.dataset.tema = siguiente;
    try { localStorage.setItem("tema", siguiente); } catch (e) { /* modo privado */ }
  });
  try {
    var guardado = localStorage.getItem("tema");
    if (guardado) document.documentElement.dataset.tema = guardado;
  } catch (e) { /* modo privado */ }

  sql.value = RECETAS[4][1];
  repintar();
  cargarTablas();
})();
