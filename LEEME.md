# Vigilante de cargadores Iberdrola (Alcoy)

Le escribes `/vigilar` a tu bot de Telegram y, en cuanto una toma de los cargadores de Iberdrola de Alcoy queda libre, te llega un aviso con el enlace al mapa. Entonces abres la app de Iberdrola y la reservas.

**Comandos del bot**

| Comando | Qué hace |
|---|---|
| `/vigilar` | Vigila hasta 3 horas y avisa cuando se libere una toma |
| `/vigilar 2` | Lo mismo, pero durante 2 horas (máximo 5,5) |
| `/parar` | Cancela la vigilancia |
| `/estado` | Te manda cómo están todos los cargadores ahora |

Todo funciona gratis: Telegram, GitHub (hace las consultas) y Cloudflare (recibe tus comandos).

```
Tú (Telegram) ──/vigilar──▶ Cloudflare Worker ──arranca──▶ GitHub Actions
                                                              │ consulta cada minuto
                                                              ▼
Tú (Telegram) ◀────────── "¡Cargador libre!" ──────── web de Iberdrola
```

> Hazlo desde un ordenador: son unos 20 minutos y en el móvil es incómodo.

---

## 1. Crear el bot de Telegram (2 min)

1. En Telegram, abre **@BotFather** y escribe `/newbot`.
2. Ponle un nombre (p. ej. *Cargador Alcoy*) y un usuario que acabe en `bot`.
3. Te dará un **token** del tipo `123456:ABC-DEF…`. Guárdalo: es el `TELEGRAM_TOKEN`.

## 2. Subir el código a GitHub (5 min)

1. Crea una cuenta en [github.com](https://github.com) si no tienes.
2. **New repository** → nombre `vigilante-alcoy` → márcalo **Public** → *Create*.
   - Público = minutos ilimitados gratis. Solo se ve el código; tus claves quedan ocultas.
   - Si lo prefieres privado, tienes 2.000 min/mes gratis (unas 33 h de vigilancia).
3. Pulsa **uploading an existing file** y arrastra el contenido de esta carpeta **excepto** la carpeta `worker`: `vigilar.py`, `requirements.txt` y la carpeta `.github` (si tu ordenador oculta `.github`, créalo a mano: *Add file → Create new file*, escribe como nombre `.github/workflows/vigilar.yml` y pega su contenido).
4. *Commit changes*.

## 3. Crear un token de GitHub (2 min)

1. GitHub → tu foto → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
2. *Repository access*: **Only select repositories** → `vigilante-alcoy`.
3. *Permissions → Repository permissions → Actions*: **Read and write**.
4. Caducidad: la máxima que te deje. Genera y copia el token: es el `GH_TOKEN`.

## 4. Crear el receptor en Cloudflare (5 min)

1. Crea una cuenta en [dash.cloudflare.com](https://dash.cloudflare.com).
2. **Workers & Pages → Create → Create Worker** → nombre `vigilante-alcoy` → *Deploy*.
3. **Edit code**: borra lo que hay, pega el contenido de `worker/worker.js` y pulsa **Deploy**.
4. Ve a **Settings → Variables and Secrets** y añade:

| Nombre | Tipo | Valor |
|---|---|---|
| `TELEGRAM_TOKEN` | Secret | el token del paso 1 |
| `WEBHOOK_SECRET` | Secret | una palabra inventada, solo letras y números (p. ej. `alcoy2026xyz`) |
| `GH_TOKEN` | Secret | el token del paso 3 |
| `GH_REPO` | Text | `tu-usuario-de-github/vigilante-alcoy` |

5. Abre en el navegador `https://vigilante-alcoy.<tu-subdominio>.workers.dev/configurar` (la dirección exacta sale en la página del Worker). Debe decir **✅ Listo**.

## 5. Sacar tu chat id y terminar (2 min)

1. En Telegram, abre tu bot y escribe `/start`. Te contestará con **tu chat id** (un número).
2. En **Cloudflare** (Settings → Variables) añade `TELEGRAM_CHAT_ID` (Text) con ese número.
3. En **GitHub**, en tu repositorio → **Settings → Secrets and variables → Actions → New repository secret**, crea dos:
   - `TELEGRAM_TOKEN` = el token del bot
   - `TELEGRAM_CHAT_ID` = tu chat id

## 6. Probar

Escribe `/estado` al bot. En 1-2 minutos te llegará la lista de cargadores de Iberdrola de Alcoy con su estado. Si llega, ya está: usa `/vigilar` cuando lo necesites.

---

## Ajustes opcionales

En GitHub → **Settings → Secrets and variables → Actions → pestaña Variables**:

- `CUPR_IDS`: vigilar solo algunos cargadores. Pon sus ids separados por comas (salen en `/estado` entre paréntesis), p. ej. `123456,123457`.
- Por defecto solo vigila las tomas **gratuitas** y no cuenta los enchufes **Schuko** (domésticos, 2,3 kW). Pon `SOLO_GRATIS` = `0` para incluir las de pago (Mercadona, Burger King…) o `EXCLUIR_TIPOS` = `ninguno` para incluir los Schuko.
- `INTERVALO`: segundos entre consultas (por defecto 60, mínimo 30).
- `ZONA`: otra zona, como `lat_min,lat_max,lon_min,lon_max`.

## Si algo falla

- **El bot no contesta a nada**: vuelve a abrir la dirección `/configurar` del paso 4.
- **"GitHub respondió 404/403"**: revisa `GH_REPO` y que el token tenga *Actions: Read and write* sobre ese repositorio.
- **"Iberdrola está rechazando mis consultas"**: la web de Iberdrola tiene un filtro antibots. Desde GitHub suele funcionar, pero si se vuelve habitual, la alternativa es ejecutar `vigilar.py` en un ordenador de casa.
- **Ver qué ha pasado**: en GitHub, pestaña **Actions**, cada vigilancia aparece con su registro.

**Ten en cuenta**: usa la misma consulta que el mapa público de iberdrola.es, que no es oficial. Si Iberdrola la cambia, el bot te avisará de que no puede consultar. Desde que pulsas `/vigilar` hasta la primera consulta pasan unos 30-60 s, y después consulta cada minuto.
