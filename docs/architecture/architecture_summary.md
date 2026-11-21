# Cómo está construido el benchmark de reportes policiales

**Resumen ejecutivo · Arquitectura local verificada en F3-19**

El benchmark es una **aplicación local en Python organizada en módulos**. Reúne herramientas para preparar insumos, revisar borradores y conservar decisiones y resultados verificables. Los módulos colaboran dentro de una invocación de Python. El navegador y el servidor de páginas de prueba tienen límites propios. Los scripts de generación y evaluación se ejecutan por separado y comparten con la revisión el formato de los archivos.

## Qué módulos lo componen

| Parte | Responsabilidad |
|---|---|
| **Interfaces de operación** | La CLI portable verifica el entorno y prepara una copia de los insumos. La CLI manual supervisa la importación de paquetes de revisión y adjudicación en un entorno de pruebas local. |
| **Coordinación** | O-01 inspecciona qué trabajo puede hacerse. O-02 verifica que la decisión siga siendo válida y usa un bloqueo para evitar que dos ejecuciones cooperantes modifiquen simultáneamente el mismo entorno. |
| **Servicios de aplicación** | Gestionan la revisión y la adjudicación, verifican las identidades de los resultados y aplican la decisión a los historiales y perfiles. |
| **Contratos y adaptadores** | Los contratos definen qué datos recibe cada revisor o adjudicador y qué debe devolver. Los adaptadores concretos traducen paquetes de operador o resultados simulados al formato común. Así se puede sustituir el adaptador sin cambiar el publicador, si el contrato se mantiene. |
| **Navegador y almacenamiento** | Playwright y Chromium sirven a las pruebas de páginas locales. El sistema de archivos conserva los insumos, resultados, manifiestos, historiales, perfiles y evidencia de aplicación. El navegador tiene su propia raíz privada y un bloqueo separado. |

La revisión define un conjunto de siete piezas por revisor. Los conjuntos se vinculan con el caso, la corrida y el intento. La adjudicación requiere los tres conjuntos completos. En la vista técnica, estas responsabilidades se muestran mediante dependencias entre módulos y contratos. El orden de ejecución queda descrito en el informe como contexto.

## Qué facilita el diseño y dónde están sus límites

- **Sustitución de revisores y adjudicadores.** El contrato común permite probar las mismas reglas con paquetes manuales y simulaciones. El acceso a archivos está más acoplado: cambiar el almacenamiento exigiría revisar servicios, validadores y mecanismos de recuperación.
- **Integridad local y recuperación.** Los manifiestos identifican cada conjunto y verifican sus archivos mediante huellas SHA-256. Los reemplazos se hacen archivo por archivo. Durante una interrupción pueden coexistir destinos ya aplicados y pendientes. La evidencia de cierre acredita que la aplicación se completó.
- **Trazabilidad con alcance definido.** F3-19 demostró que se puede reconstruir una representación de la solicitud sintética usando los insumos originales y los manifiestos. El texto literal enviado al navegador de prueba y los bytes previos del perfil no quedan conservados. Los hashes verifican integridad local y la autoría comercial sigue sin certificar.
- **Entorno comprobado.** Las pruebas previas verificaron la ruta manual y la simulación con Chromium en macOS. Los conectores a proveedores reales, Linux, Windows y el despliegue remoto carecen de validación de ejecución en este alcance.

El [diagrama editable](benchmark_architecture.drawio) contiene tres vistas: organización del sistema, componentes y dependencias, y despliegue y persistencia locales. El [informe técnico](architecture_analysis.md) relaciona los módulos y riesgos con el código y las pruebas.
