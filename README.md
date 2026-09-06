# Dashboard FFJ (Meta + TikTok Ads)

Dashboard estático que se actualiza solo cada día a las 6:00 (hora España)
vía GitHub Actions, sin ninguna acción manual una vez configurado.

## Configuración inicial (una sola vez)

1. **Crea un repositorio nuevo en GitHub** (puede ser privado) y sube todo
   el contenido de esta carpeta tal cual.

2. **Añade el secret `WINDSOR_API_KEY`**:
   - Ve a `Settings` → `Secrets and variables` → `Actions` → `New repository secret`.
   - Nombre: `WINDSOR_API_KEY`. Valor: tu API key de Windsor.ai (la
     encuentras en tu panel de Windsor.ai, sección API).

3. **Activa GitHub Pages**:
   - Ve a `Settings` → `Pages`.
   - En "Source", elige **GitHub Actions** (no "Deploy from a branch").

4. **Lanza el workflow una vez a mano** para generar los datos iniciales:
   - Ve a la pestaña `Actions` → `Actualización diaria del dashboard` → `Run workflow`.
   - Cuando termine (1-2 minutos), tu dashboard estará publicado en la URL
     que indique la pestaña `Pages` de `Settings` (algo como
     `https://tu-usuario.github.io/tu-repo/`).

A partir de ahí, el workflow corre solo cada día a las 6:00 (hora España,
con el matiz de una hora en horario de verano — ver comentario en el
propio archivo del workflow) y el dashboard se ve siempre actualizado
hasta el día anterior, en la misma URL, sin que nadie tenga que tocar nada.

## Estructura

- `index.html` — el dashboard (no se toca a mano; lee los datos con `fetch()`).
- `data_meta.json`, `data_tt.json` — datos de Meta y TikTok, sobrescritos
  a diario por el workflow.
- `scripts/update_data.py` — script que pide los datos a Windsor.ai y
  regenera los JSON. Verifica automáticamente que los totales cuadren
  antes de sobrescribir nada (si algo no cuadra, el workflow falla y
  **no** publica datos rotos).
- `.github/workflows/daily-update.yml` — la automatización diaria.

## Pendiente (próxima entrega)

- Tabla semanal en la pestaña "Comparativa 2025" (hoy solo está la mensual).
- Pestaña nueva "Insights semanales" (Meta y TikTok por separado, con el
  análisis causal tipo media buyer que definiste, actualizado los lunes).
  Esta parte necesita razonamiento, no solo aritmética, así que se
  automatiza aparte (vía una tarea programada de Cowork que escribe un
  `insights_meta.json` / `insights_tt.json` adicional).
