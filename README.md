# ODB Paster

Herramienta de escritorio para Windows que ayuda a capturar datos de referencias y completarlos automáticamente en el formulario de ODB.

## Funciones

- Editor simple con tema oscuro y captura automática desde el portapapeles.
- Reconoce automaticamente Demographics basicos como MRNs, nombres, correos y celulares.
- Automatiza el volcado de data en el formulario del navegador mediante Playwright.
- Configuración externa editable para el puerto y la URL del formulario.

## Requisitos

- Windows
- Cualquier navegador basado en chromium (Chrome, opera, edge, brave, etc...)

## Requisitos para build

- Python 3
- Playwright para Python
- PyInstaller, solo para crear el ejecutable

Instala las dependencias:

```bat
python -m pip install -r requirements-build.txt
python -m playwright install chromium
```

## Configuración

Edita `config.json`:

```json
{
  "browser": {
    "host": "localhost",
    "port": 9222
  },
  "form_url": "test.enterpriseurl.com" #colocar URL empresarial acá
}
```

Inicia el navegador con depuración remota activa en el puerto configurado y abre la URL del formulario antes de ejecutar la automatización.

## Ejecución

Doble click en el .exe, o:
```bat
python main.py
```

Pega o escribe los datos en el editor. Cuando estés listo, usa `Ctrl+Shift+F` o ejecuta **Actions > Dunk on ODB** para comenzar el volcado de data. Presiona `Esc` para cancelar.

## Crear el ejecutable

Desde una terminal de Windows, ejecuta:

```bat
build.bat
```

El resultado se genera en `dist\ODB Paster\`. `config.json` se copia automáticamente a esa carpeta.