import json
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


class AutomationCancelled(Exception):
    pass


def load_connection_config():
    config_directory = (
        Path(sys.executable).resolve().parent
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parent
    )
    config_path = config_directory / "config.json"

    try:
        with config_path.open(encoding="utf-8") as config_file:
            config = json.load(config_file)
    except FileNotFoundError as error:
        raise FileNotFoundError(
            f"Configuration file not found: {config_path}"
        ) from error
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Invalid JSON in {config_path}: {error}"
        ) from error

    if not isinstance(config, dict):
        raise ValueError(f"Configuration must be a JSON object: {config_path}")

    browser = config.get("browser")
    if not isinstance(browser, dict):
        raise ValueError(
            f"'browser' must be an object in {config_path}"
        )

    host = browser.get("host")
    port = browser.get("port")
    form_url = config.get("form_url")
    if not isinstance(host, str) or not host.strip():
        raise ValueError(
            f"'browser.host' must be a non-empty string in {config_path}"
        )
    if (
        not isinstance(port, int)
        or isinstance(port, bool)
        or not 1 <= port <= 65535
    ):
        raise ValueError(
            f"'browser.port' must be an integer from 1 to 65535 in {config_path}"
        )
    if not isinstance(form_url, str):
        raise ValueError(f"'form_url' must be a string in {config_path}")
    parsed_url = urlsplit(form_url)
    if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
        raise ValueError(
            f"'form_url' must be an absolute HTTP or HTTPS URL in {config_path}"
        )

    return f"http://{host.strip()}:{port}", form_url


def check_cancelled(cancel_event, page=None):
    browser_cancelled = (
        page.evaluate("Boolean(window.__odbAutomationCancelled)")
        if page is not None
        else False
    )
    if browser_cancelled or (
        cancel_event is not None and cancel_event.is_set()
    ):
        raise AutomationCancelled("ODB automation cancelled with Escape.")


def wait_or_cancel(seconds, cancel_event, page):
    deadline = time.monotonic() + seconds
    while True:
        check_cancelled(cancel_event, page)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.05, remaining))


def build_referral_template(referral_source, ref_type):
    if referral_source not in ("CCD", "FAX"):
        raise ValueError(f"Unsupported referral source: {referral_source}")
    if ref_type not in ("OV", "COL"):
        raise ValueError(f"Unsupported referral type: {ref_type}")

    gen_val, det_val = (
        ("proc", "col") if ref_type == "COL" else ("off", "off")
    )
    return {
        "Client": "bor",
        "Project Name": "bor",
        "Referral Source": referral_source,
        "Referral Type General": gen_val,
        "Referral Type Detail": det_val
    }


def new_patient_value(value):
    values = {
        "new": "yes",
        "old": "no",
        "yes": "yes",
        "no": "no",
    }
    try:
        return values[value.strip().casefold()]
    except KeyError as error:
        raise ValueError(f"Unsupported New Patient value: {value}") from error


def normalize_provider_value(value):
    normalized = value.strip()
    if normalized.casefold() in ("bg", "fl"):
        return "FL"
    return normalized


def ejecutar_automatizacion(
    datos_archivo,
    referral_source,
    ref_type,
    cancel_event=None,
):
    check_cancelled(cancel_event)
    plantilla = build_referral_template(referral_source, ref_type)
    browser_url, url_formulario = load_connection_config()
    field_errors = []

    # 2. Conexión con el navegador configurado
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(browser_url)
            
            page = None
            for context in browser.contexts:
                for p_tab in context.pages:
                    if url_formulario in p_tab.url:
                        page = p_tab
                        break
                if page:
                    break
            
            if not page:
                error_msg = f"No se encontró ninguna pestaña abierta con la URL:\n{url_formulario}\n\nAbre la pestaña antes de ejecutar el script."
                print(f"❌ {error_msg}")
                raise RuntimeError(error_msg)

            print(f"\n🔗 Pestaña encontrada y reutilizada: {page.url}")
            page.evaluate(
                """() => {
                    window.__odbAutomationCancelled = false;
                    window.addEventListener("keydown", event => {
                        if (event.key === "Escape") {
                            window.__odbAutomationCancelled = true;
                        }
                    }, true);
                }"""
            )
            page.bring_to_front()
            page.wait_for_load_state("networkidle")
            check_cancelled(cancel_event, page)

            # --- FASE 1: PLANTILLA ---
            print(f"\n⚙️ Fase 1: Rellenando campos de plantilla ({referral_source}/{ref_type})...")
            for nombre_campo, valor_busqueda in plantilla.items():
                try:
                    check_cancelled(cancel_event, page)
                    label_elem = page.locator(f"label:has-text('{nombre_campo}')")
                    dropdown = label_elem.locator("xpath=following-sibling::div[contains(@class, 'container') or contains(@class, 'control')]").first
                    dropdown.click()
                    check_cancelled(cancel_event, page)
                    page.keyboard.type(valor_busqueda)
                    wait_or_cancel(0.3, cancel_event, page)
                    page.keyboard.press("Enter")
                    wait_or_cancel(0.5, cancel_event, page)
                except AutomationCancelled:
                    raise
                except Exception as e:
                    print(f"⚠️ Advertencia en plantilla {nombre_campo}: {e}")
                    field_errors.append(f"{nombre_campo}: {e}")

            # --- FASE 2: DATOS DEL EDITOR ---
            print("\n📋 Fase 2: Rellenando datos del editor...")

            check_cancelled(cancel_event, page)
            if datos_archivo["record_Number"]:
                page.fill('input[name="record_Number"]', datos_archivo["record_Number"])

            check_cancelled(cancel_event, page)
            if datos_archivo["New_Patient"]:
                try:
                    patient_status = new_patient_value(
                        datos_archivo["New_Patient"]
                    )
                    label_elem = page.locator("label:has-text('New Patient')")
                    dropdown = label_elem.locator("xpath=following-sibling::div[contains(@class, 'container') or contains(@class, 'control')]").first
                    dropdown.click()
                    check_cancelled(cancel_event, page)
                    page.keyboard.type(patient_status)
                    wait_or_cancel(0.3, cancel_event, page)
                    page.keyboard.press("Enter")
                    wait_or_cancel(0.5, cancel_event, page)
                except AutomationCancelled:
                    raise
                except Exception as e:
                    print(f"⚠️ Error en New Patient: {e}")
                    field_errors.append(f"New Patient: {e}")

            check_cancelled(cancel_event, page)
            if datos_archivo.get("referral_receipt_date"):
                try:
                    print(f" -> Escribiendo Referral Receipt Date (Forzando clic al inicio): {datos_archivo['referral_receipt_date']}")
                    date_input = page.locator('input[name="referral_receipt_date"]')
                    
                    # Forzamos el clic en la misma esquina izquierda para mantener consistencia total
                    date_input.click(position={"x": 5, "y": 10})
                    
                    date_input.press("Control+A")
                    date_input.press("Backspace")
                    check_cancelled(cancel_event, page)
                    page.keyboard.type(datos_archivo["referral_receipt_date"], delay=50)
                    wait_or_cancel(0.3, cancel_event, page)
                except AutomationCancelled:
                    raise
                except Exception as e:
                    print(f"⚠️ Error en referral_receipt_date: {e}")
                    field_errors.append(f"Referral Receipt Date: {e}")

            # Diagnóstico con la regla especial de 'other'
            check_cancelled(cancel_event, page)
            if datos_archivo.get("Diagnostic"):
                try:
                    print(f" -> Seleccionando Diagnostic: {datos_archivo['Diagnostic']}")
                    label_elem = page.locator("label:has-text('Diagnostic')")
                    dropdown = label_elem.locator("xpath=following-sibling::div[contains(@class, 'container') or contains(@class, 'control')]").first
                    dropdown.click()
                    check_cancelled(cancel_event, page)
                    page.keyboard.type(datos_archivo["Diagnostic"])
                    wait_or_cancel(0.4, cancel_event, page)
                    
                    if "other" in datos_archivo["Diagnostic"].lower():
                        page.keyboard.press("ArrowDown")
                        wait_or_cancel(0.2, cancel_event, page)
                    
                    page.keyboard.press("Enter")
                    wait_or_cancel(0.5, cancel_event, page)
                except AutomationCancelled:
                    raise
                except Exception as e:
                    print(f"⚠️ Error en Diagnostic: {e}")
                    field_errors.append(f"Diagnostic: {e}")

            check_cancelled(cancel_event, page)
            if datos_archivo.get("patient"):
                page.fill('input[name="patient"]', datos_archivo["patient"])

            check_cancelled(cancel_event, page)
            if datos_archivo.get("dob"):
                try:
                    print(f" -> Escribiendo DOB (Forzando clic al inicio): {datos_archivo['dob']}")
                    dob_input = page.locator('input[name="dob"]')
                    
                    # Forzamos el clic exactamente a 5 píxeles del borde izquierdo
                    dob_input.click(position={"x": 5, "y": 10})
                    
                    dob_input.press("Control+A")
                    dob_input.press("Backspace")
                    check_cancelled(cancel_event, page)
                    page.keyboard.type(datos_archivo["dob"], delay=50)
                    wait_or_cancel(0.3, cancel_event, page)
                except AutomationCancelled:
                    raise
                except Exception as e:
                    print(f"⚠️ Error en dob: {e}")
                    field_errors.append(f"DOB: {e}")

            check_cancelled(cancel_event, page)
            if datos_archivo["phone"]:
                page.fill('textarea[name="phone"], [name="phone"]', datos_archivo["phone"])

            check_cancelled(cancel_event, page)
            if datos_archivo["email"]:
                page.fill('input[name="email"], textarea[name="email"]', datos_archivo["email"])

            check_cancelled(cancel_event, page)
            if datos_archivo["provider"]:
                try:
                    label_elem = page.locator("label:has-text('Provider')")
                    dropdown = label_elem.locator("xpath=following-sibling::div[contains(@class, 'container') or contains(@class, 'control')]").first
                    dropdown.click()
                    check_cancelled(cancel_event, page)
                    page.keyboard.type(
                        normalize_provider_value(datos_archivo["provider"])
                    )
                    wait_or_cancel(0.3, cancel_event, page)
                    page.keyboard.press("Enter")
                    wait_or_cancel(0.5, cancel_event, page)
                except AutomationCancelled:
                    raise
                except Exception as e:
                    print(f"⚠️ Error en Provider: {e}")
                    field_errors.append(f"Provider: {e}")

            check_cancelled(cancel_event, page)
            if field_errors:
                details = "\n".join(field_errors)
                raise RuntimeError(
                    "Some form fields could not be completed. "
                    "The form may be partially filled:\n" + details
                )

            print("\n🎉 ¡Proceso de automatización finalizado con éxito!")

        except AutomationCancelled:
            raise
        except Exception as e:
            error_general = f"Ocurrió un error inesperado durante la ejecución:\n{e}"
            print(f"❌ {error_general}")
            raise RuntimeError(error_general) from e
