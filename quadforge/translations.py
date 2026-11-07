"""Spanish translation of the interface.

Blender shows it automatically when its language is set to Spanish
(Preferences > Interface > Language). The English texts come from ``texts.py``
and are used as dictionary keys, so they always match.

To add another language, copy ``SPANISH_FROM_TEXTS`` and ``SPANISH_INTERFACE`` and
translate the values. Keep every ``{placeholder}`` exactly as it is.
"""
import bpy

from . import texts

# Texts that live in texts.py (guidance, help, labels).
SPANISH_FROM_TEXTS = {
    texts.GUIDE_SELECT_MESH: "Paso 1: haz clic en la malla que quieres remallar.",
    texts.GUIDE_NOT_A_MESH: "'{name}' es un objeto de tipo {kind}. Selecciona un objeto de malla.",
    texts.GUIDE_NO_FACES: "'{name}' no tiene caras para remallar.",
    texts.GUIDE_READY: "Listo: '{name}' tiene {faces} caras y se convertirá en unos {target} {kind}.",
    texts.GUIDE_TARGET_NOT_LOWER: (
        "El objetivo ({target}) no es menor que el detalle que ya tiene esta malla (unos {faces} quads), "
        "así que el resultado no será más simple."),
    texts.GUIDE_HEAVY_MESH: (
        "Esta malla es muy densa ({faces} caras) y la copia de trabajo está desactivada. Remallar puede "
        "tardar varios minutos y usar mucha memoria. Activa 'Usar copia de trabajo' o decímala antes."),
    texts.GUIDE_USES_PROXY: (
        "Este sculpt es pesado ({faces} caras). QuadForge primero hará una copia de trabajo más ligera de "
        "unas {proxy} caras (más rápido y con mucha menos memoria) y luego ajustará el resultado a tu "
        "sculpt original para conservar el detalle."),
    texts.GUIDE_PROXY_SMALL: (
        "La copia de trabajo ({proxy} caras) es pequeña para un objetivo de {target} quads. "
        "Usa al menos 3 o 4 veces el objetivo, o activa 'Tamaño automático', para conservar el detalle."),
    texts.GUIDE_LEVELS_IGNORED: (
        "Se ignoraron estos niveles extra (no son un número, no son menores que el nivel anterior, "
        "o son demasiado pequeños): {items}."),
    texts.GUIDE_MODIFIERS: "{count} modificador(es) se incluirán en el resultado. Tu objeto original no cambia.",
    texts.GUIDE_LEAVES_MODE: "QuadForge pasará a Modo Objeto antes de remallar.",
    texts.GUIDE_OFF_CENTER: (
        "El modelo no está centrado en el eje {axis} (el origen del objeto está desplazado). "
        "Cambia 'Plano en' a Centro de la caja envolvente, o mueve el origen al centro del modelo."),
    texts.GUIDE_NO_MATERIAL_ZONES: "'Por material' está activo, pero esta malla solo tiene un material: no hay nada que separar.",
    texts.GUIDE_NO_FACE_SETS: "'Por Face Set' está activo, pero esta malla no tiene Face Sets de sculpt.",
    texts.GUIDE_NO_MASK: "'Por máscara de sculpt' está activo, pero esta malla no tiene máscara de sculpt.",

    texts.LABEL_SURFACE: "Tipo de superficie (configura las opciones por ti)",
    texts.LABEL_HEAVY_TARGET: "Objetivo grande: lento y pesado en memoria.",
    texts.LABEL_EXACT_NUMBER: "Número exacto",
    "Hard Surface": "Superficie dura",
    "Soft Surface / Organic": "Superficie blanda / Orgánica",
    texts.LABEL_QUICK_PRESETS: "Presets rápidos (ajustan objetivo y ángulo con un clic)",
    texts.LABEL_ACTIVE_OPTIONS: "Opciones activas: {options}",
    texts.LABEL_NO_OPTIONS: "ninguna (solo la cantidad objetivo)",
    texts.LABEL_OPTION_HARD_EDGES: "bordes duros sobre {angle} grados",
    texts.LABEL_OPTION_ZONES: "zonas: {zones}",
    texts.LABEL_OPTION_SYMMETRY: "simetría {axes}",
    texts.LABEL_OPTION_LEVELS: "niveles: {levels}",
    texts.HINT_OPTIONAL_PANELS: "Los ajustes opcionales están en los paneles de abajo.",

    texts.HELP_HARD_EDGES: (
        "Los bordes duros son las esquinas marcadas de tu modelo. QuadForge los conserva como "
        "loops de aristas limpios en lugar de redondearlos. Baja el ángulo para conservar más "
        "bordes, súbelo para conservar menos."),
    texts.HELP_ZONES: (
        "Las zonas son áreas que quieres mantener separadas, para que el flujo de quads no "
        "cruce de una a otra. Activa una o más opciones."),
    texts.HELP_SMART_FEATURES: (
        "La detección inteligente analiza cadenas completas de bordes duros en vez de bordes sueltos. Ignora "
        "bultos pequeños que los quads nunca podrían seguir, conserva las esquinas rectas y ajusta las esquinas "
        "del resultado sobre las líneas reales de borde de tu modelo para que queden nítidas."),
    texts.HELP_SYMMETRY: (
        "Usa simetría cuando tu modelo es un espejo de sí mismo. Se remalla la mitad y luego se "
        "espeja, así ambos lados quedan exactamente iguales. Los ejes son los ejes locales del objeto."),
    texts.HELP_SYMMETRY_PLANE: (
        "El plano de espejo pasa por el origen del objeto. Si el origen no está en el centro del "
        "modelo, elige 'Centro de la caja envolvente'."),
    texts.HELP_SYMMETRY_SIDE: "La mitad que no conservas se descarta, así que conserva tu mejor lado.",
    texts.HELP_SPEED: (
        "Los sculpts pesados se remallan a través de una copia de trabajo más ligera (por ejemplo de 3 millones "
        "de caras a 80,000), que es mucho más rápida y usa mucha menos memoria. Después el resultado se ajusta "
        "a tu sculpt original para conservar el detalle. Desactívala solo con mallas ligeras."),
    texts.HELP_POLISH: (
        "Cada pasada iguala el tamaño de los quads y los ajusta de nuevo a tu sculpt. Las esquinas y los "
        "bordes abiertos nunca se mueven. De 2 a 4 pasadas dan un flujo regular y agradable; con 0 solo se ajusta."),
    texts.HELP_LEVELS: (
        "Construye varias versiones de una vez, desde el objetivo de arriba hasta cantidades menores. Escribe "
        "los niveles extra separados por comas, por ejemplo 15K, 10K, 5K, 4K. Cada nivel se hace a partir del "
        "anterior y se ajusta a tu sculpt original."),
    texts.LABEL_LEVELS_PREVIEW: "Niveles a construir: {levels}",

    texts.LABEL_RESULT_FACES: "{faces} caras",
    texts.LABEL_RESULT_QUADS: "{quads} quads ({percent}%)",
    texts.LABEL_RESULT_TRIS: "{tris} triángulos, {ngons} n-gons",
    texts.LABEL_RESULT_POLES: "{poles} polos (vértices que no son de 4 aristas)",
    texts.LABEL_RESULT_HARD_EDGES: "{count} bordes duros conservados",
    texts.LABEL_RESULT_TIME: "Tiempo: {seconds} s",
    texts.LABEL_RESULT_CORNERS: "{count} vértices de esquina ajustados a líneas de borde",
    texts.LABEL_RESULT_MEMORY: "Memoria máxima de Blender: {memory} GB",
    texts.LABEL_RESULT_LEVELS: "Todos los niveles",
    texts.LABEL_RESULT_LEVEL_LINE: "{name}: {quads} quads ({percent}%), {poles} polos, {seconds} s",
    texts.HELP_RESULT: "Buena topología: casi todo quads y pocos polos.",

    texts.GUIDE_TITLE_STEPS: "Paso a paso",
    texts.GUIDE_STEP_1: "1. Selecciona la malla que esculpiste. Puede ser de triángulos o quads, con cualquier densidad.",
    texts.GUIDE_STEP_2: "2. Elige un preset, o elige Quads o Triángulos en 'Salida' y escribe cuántas caras quieres en 'Caras objetivo'.",
    texts.GUIDE_STEP_3: "3. Opcional: abre 'Bordes duros' para conservar esquinas marcadas. Un ángulo menor conserva más bordes.",
    texts.GUIDE_STEP_4: "4. Opcional: abre 'Aislar zonas' para mantener separados materiales, Face Sets o una máscara de sculpt.",
    texts.GUIDE_STEP_5: "5. Opcional: abre 'Simetría' y activa los ejes en los que tu modelo es un espejo. X es lo más común.",
    texts.GUIDE_STEP_6: (
        "6. Opcional: 'Niveles extra' construye versiones más bajas de una vez (por ejemplo 15K, 10K, 5K, 4K). "
        "'Velocidad y detalle' controla la copia de trabajo y las pasadas de pulido."),
    texts.GUIDE_STEP_7: (
        "7. Pulsa 'Remallar'. Tu original se queda (oculto) y aparece un objeto nuevo "
        "llamado '<nombre>_quads' o '<nombre>_tris' (uno por nivel)."),
    texts.LABEL_RESULT_TRI_ONLY: "Malla de triángulos: {tris} triángulos, de tamaño uniforme",
    texts.HELP_ENGINE_FORGE: (
        "FORGE es el motor propio de QuadForge. Lee el sculpt directamente (sin copia de trabajo), conserva "
        "bordes duros y bordes abiertos, y puede sacar quads o triángulos. Se ejecuta en un proceso aparte, "
        "así que no puede cerrar Blender."),
    texts.HELP_DENSITY: (
        "Pinta un vertex group en modo Weight Paint: las caras serán más pequeñas donde el peso es alto y "
        "normales donde es 0. Elige el grupo aquí."),
    texts.HELP_ZONE_DETAIL: (
        "Da a cada zona su propio nivel de detalle. Primero activa una opción en Aislar zonas, luego pulsa "
        "Actualizar zonas y fija un número por zona: por encima de 1 esa zona tiene más caras, por debajo menos. "
        "El motor mantiene un flujo limpio de quads, así que el cambio es gradual (un 2 da aproximadamente "
        "1,7 veces más caras en la zona)."),
    texts.HELP_GUIDES: (
        "Las guías de flujo le dicen al motor por dónde quieres que corran las filas de quads, por ejemplo "
        "alrededor de un ojo o a lo largo de la mandíbula. En Modo Edición selecciona aristas de tu modelo y "
        "pulsa Marcar guía de flujo. Son una sugerencia suave: el motor las sigue donde la superficie lo "
        "permite. Con simetría activa, se reflejan."),
    texts.LABEL_GUIDE_COUNT: "{count} aristas guía en este objeto.",
    texts.LABEL_GUIDE_NONE: "Aún no hay aristas guía. Selecciona aristas en Modo Edición y pulsa Marcar guía de flujo.",
    texts.HELP_CURVATURE: (
        "Detalle en curvas pone caras más pequeñas donde la superficie se curva con fuerza (párpados, labios, "
        "fosas nasales, borde de las orejas) y hace que las filas de caras sigan esas líneas, sin pintar nada. Prueba Medio en caras y personajes. Puede añadir "
        "algunos vértices irregulares en otras zonas, así que déjalo en Off para formas simples o mecánicas."),
    texts.LABEL_NO_GROUPS: "Este objeto aún no tiene vertex groups.",
    texts.GUIDE_STEP_8: (
        "8. Lee 'Último resultado': un porcentaje alto de quads y pocos polos significan topología "
        "limpia. Los avisos indican qué ajustar."),
    texts.GUIDE_TITLE_TIPS: "Si el resultado no es el esperado",
    texts.GUIDE_TIP_1: "Demasiados o muy pocos loops en las esquinas: cambia el ángulo de Bordes duros.",
    texts.GUIDE_TIP_2: "Cantidad de quads lejos del objetivo: los bordes duros y las zonas añaden restricciones. Sube el ángulo o desactiva las zonas.",
    texts.GUIDE_TIP_3: "Una costura visible en el centro de un modelo simétrico: revisa que 'Plano en' coincida con el centro del modelo.",
    texts.GUIDE_TIP_4: "El remallado falla: cierra agujeros, borra partes sueltas y geometría non-manifold, y vuelve a intentar.",
    texts.GUIDE_TIP_5: (
        "Parece que se perdió el detalle fino del sculpt: una malla de quads con pocos polígonos no puede contenerlo. "
        "Deja activo 'Conservar detalle del sculpt' y recupera el detalle con un reshape de Multires o un bake de normal map."),

    texts.CONFIRM_TITLE: "Malla muy densa",
    texts.CONFIRM_MESSAGE: "Esto puede tardar varios minutos y usar mucha memoria. ¿Continuar?",
    texts.CONFIRM_BUTTON: "Remallar de todos modos",
}

# Names and tooltips of properties, panels and buttons. The English text must match
# properties.py / ui.py / operators.py exactly.
SPANISH_INTERFACE = {
    # panels and buttons
    "Hard Edges": "Bordes duros",
    "Isolate Zones": "Aislar zonas",
    "Symmetry": "Simetría",
    "Extra Levels": "Niveles extra",
    "Speed and Detail": "Velocidad y detalle",
    "Last Result": "Último resultado",
    "How to Use": "Cómo usar",
    "Advanced": "Avanzado",
    "Axes": "Ejes",
    "Remesh": "Remallar",
    "Detail by Zone": "Detalle por zona",
    "Refresh Zones": "Actualizar zonas",
    "Subdivision Match": "Ajuste para subdivisión",
    "Move the vertices so that, once you add a Subdivision Surface modifier, the smooth result lies on your original surface instead of shrinking inside it. Made for smooth, organic shapes; quads only. Sharp creases stay where they are, and no point moves more than a third of an edge": "Mueve los vértices para que, al añadir un modificador Subdivision Surface, el resultado suave quede sobre tu superficie original en vez de encogerse. Pensado para formas orgánicas suaves; solo quads. Los pliegues marcados no se mueven y ningún punto se desplaza más de un tercio de arista",
    "Quick Preview": "Vista previa rápida",
    "Make a quick low-resolution version (about 3,000 faces) with your current settings, to check the flow before the full run": "Crea una versión rápida de baja resolución (unas 3.000 caras) con tus ajustes actuales, para revisar el flujo antes del remallado completo",
    "Engine": "Motor",
    "Output": "Salida",
    "Quads": "Quads",
    "Triangles": "Triángulos",
    "Density": "Densidad",
    "Density Group": "Grupo de densidad",
    "Detail Ratio": "Proporción de detalle",
    "FORGE (QuadForge)": "FORGE (QuadForge)",
    "QuadriFlow (Blender)": "QuadriFlow (Blender)",
    "Reset Settings": "Restablecer ajustes",
    "Apply Preset": "Aplicar preset",
    "materials": "materiales",
    "face sets": "face sets",
    "mask": "máscara",
    # property names
    "Target Faces": "Caras objetivo",
    "Use Working Copy": "Usar copia de trabajo",
    "Auto Size": "Tamaño automático",
    "Working Copy Size": "Tamaño de la copia de trabajo",
    "Keep Sculpt Detail": "Conservar detalle del sculpt",
    "Polish Passes": "Pasadas de pulido",
    "Keep Hard Edges": "Conservar bordes duros",
    "Smart Detection": "Detección inteligente",
    "Minimum Feature Size": "Tamaño mínimo de detalle",
    "Keep Right Angles": "Conservar ángulos rectos",
    "Snap Corners to Edges": "Ajustar esquinas a los bordes",
    "Shading": "Sombreado",
    "Smooth with Sharp Edges": "Suave con bordes duros",
    "Smooth": "Suave",
    "Flat": "Plano",
    "Angle": "Ángulo",
    "Edges Marked Sharp": "Bordes marcados como sharp",
    "Rounded Bevels": "Biseles redondeados",
    "UV Seams": "Costuras UV",
    "Keep Open Borders": "Conservar bordes abiertos",
    "By Material": "Por material",
    "By Face Set": "Por Face Set",
    "By Sculpt Mask": "Por máscara de sculpt",
    "By Vertex Group": "Por vertex group",
    "Group Cutoff": "Umbral de grupo",
    "Keep areas painted in different vertex groups separated by clean edge loops (each face goes with the group that has the most weight on it)":
        "Mantiene separadas con loops limpios las áreas pintadas en vertex groups distintos (cada cara va con el grupo que tiene más peso en ella)",
    "Weight a face needs in a vertex group to belong to it": "Peso que necesita una cara en un vertex group para pertenecer a él",
    "'By Vertex Group' is on, but this mesh has no vertex groups.": "'Por vertex group' está activado, pero esta malla no tiene vertex groups.",
    "vertex groups": "vertex groups",
    "Mask Cutoff": "Umbral de máscara",
    "Keep Side": "Lado a conservar",
    "Auto": "Auto",
    "Positive": "Positivo",
    "Negative": "Negativo",
    "Plane At": "Plano en",
    "Object Origin": "Origen del objeto",
    "Bounding Box Centre": "Centro de la caja envolvente",
    "Weld Distance": "Distancia de soldadura",
    "Warn If Not Symmetric": "Avisar si no es simétrico",
    "Clean Mesh First": "Limpiar la malla antes",
    "Hide Original": "Ocultar original",
    "Seed": "Semilla",
    # property tooltips
    "Approximate number of faces (quads or triangles) you want in the result. The final count can differ by a few percent":
        "Cantidad aproximada de caras (quads o triángulos) que quieres en el resultado. La cantidad final puede variar unos pocos por ciento",
    "Lower face counts to build as well, separated by commas, for example 15K, 10K, 5K, 4K. Each level is made from the previous one with QuadriFlow and from your source with FORGE":
        "Cantidades menores de caras que también se construyen, separadas por comas, por ejemplo 15K, 10K, 5K, 4K. Con QuadriFlow cada nivel sale del anterior; con FORGE, de tu fuente",
    "Which remeshing engine does the work": "Qué motor de remallado hace el trabajo",
    "QuadForge's own engine: quads or triangles, fast on heavy meshes, paintable density. Runs as a separate process":
        "El motor propio de QuadForge: quads o triángulos, rápido con mallas pesadas, densidad pintable. Se ejecuta en un proceso aparte",
    "Blender's built-in quad remesher. Quads only": "El remallador de quads integrado en Blender. Solo quads",
    "What kind of faces the result is made of": "De qué tipo de caras es el resultado",
    "Clean quad topology (for animation, subdivision, sculpting)": "Topología de quads limpia (para animación, subdivisión, sculpt)",
    "Even, well-shaped triangles (for games, printing, simulation)": "Triángulos uniformes y bien formados (para juegos, impresión, simulación)",
    "Vertex group that asks for smaller faces where its weight is high (FORGE only). Paint it in Weight Paint mode on the areas that need more detail":
        "Vertex group que pide caras más pequeñas donde su peso es alto (solo FORGE). Píntalo en modo Weight Paint en las zonas que necesitan más detalle",
    "How many times shorter the edges are where the group weight is 1 (4 means four times finer, so about 16 times more faces there)":
        "Cuántas veces más cortas son las aristas donde el peso del grupo es 1 (4 significa cuatro veces más fino, unas 16 veces más caras ahí)",
    "Remesh a lighter copy of heavy sculpts, then snap the result back onto the original. Much faster and uses far less memory":
        "Remalla una copia más ligera de los sculpts pesados y luego ajusta el resultado al original. Mucho más rápido y usa mucha menos memoria",
    "Size the working copy at 4 times the largest target (between 40,000 and 600,000 faces)":
        "Dimensiona la copia de trabajo a 4 veces el mayor objetivo (entre 40,000 y 600,000 caras)",
    "Faces of the working copy. Use at least 3 to 4 times the target to keep the detail":
        "Caras de la copia de trabajo. Usa al menos 3 o 4 veces el objetivo para conservar el detalle",
    "Mark Flow Guide": "Marcar guía de flujo",
    "Clear Flow Guides": "Borrar guías de flujo",
    "Flow Guides": "Guías de flujo",
    "Copy UV Maps": "Copiar mapas UV",
    "Copy Face Sets and Mask": "Copiar Face Sets y máscara",
    "Use Flow Guides": "Usar guías de flujo",
    "Snap the result back onto your original sculpt so it follows every curve and corner":
        "Ajusta el resultado a tu sculpt original para que siga cada curva y esquina",
    "How many times to even out the quads and snap them back onto the sculpt. 0 only snaps; 2 to 4 gives a regular flow":
        "Cuántas veces igualar los quads y ajustarlos de nuevo al sculpt. Con 0 solo se ajusta; de 2 a 4 da un flujo regular",
    "Look at whole chains of sharp edges: ignore tiny bumps, keep right-angle corners":
        "Analiza cadenas completas de bordes duros: ignora bultos pequeños y conserva las esquinas rectas",
    "Chains of sharp edges shorter than this many quad lengths are treated as noise and ignored. Lower keeps small details, higher keeps only long creases":
        "Las cadenas de bordes duros más cortas que esta cantidad de largos de quad se tratan como ruido y se ignoran. Menor conserva detalles pequeños, mayor solo creases largos",
    "Keep short runs of 90 degree corners (pegs, boxes, rims) even if they are small":
        "Conserva tramos cortos de esquinas de 90 grados (pegs, cajas, bordes) aunque sean pequeños",
    "Move the corner vertices of the result exactly onto the sharp edge lines of your model so corners stay crisp":
        "Mueve los vértices de esquina del resultado exactamente sobre las líneas de borde duro de tu modelo para que las esquinas queden nítidas",
    "How the result is shaded": "Cómo se sombrea el resultado",
    "Smooth surfaces, crisp corners (hard edges are marked sharp)": "Superficies suaves y esquinas nítidas (los bordes duros se marcan como sharp)",
    "Everything smooth": "Todo suave",
    "Every face flat": "Cada cara plana",
    "Detect sharp corners and keep them as clean edge loops":
        "Detecta esquinas marcadas y las conserva como loops de aristas limpios",
    "Edges where the surface bends more than this angle are kept. Lower keeps more edges, higher keeps fewer":
        "Se conservan las aristas donde la superficie se dobla más que este ángulo. Menor conserva más, mayor conserva menos",
    "Also keep the edges you already marked as sharp in Blender":
        "Conserva también las aristas que ya marcaste como sharp en Blender",
    "Keep the profile of rounded bevels and thin chamfer strips: the quad rows follow both sides of them and the faces get smaller there. Turn off if a smooth surface gets too many small faces":
        "Conserva el perfil de los biseles redondeados y las franjas finas de chaflán: las filas de quads siguen ambos lados y las caras se hacen más pequeñas ahí. Desactívalo si una superficie suave recibe demasiadas caras pequeñas",
    "Also keep UV seams as hard edges": "Conserva también las costuras UV como bordes duros",
    "Keep the outline of open meshes (holes and cut edges)":
        "Conserva el contorno de las mallas abiertas (agujeros y bordes cortados)",
    "Keep zones that use different materials separated by clean edge loops":
        "Mantiene separadas con loops limpios las zonas que usan materiales distintos",
    "Keep sculpt Face Sets separated by clean edge loops":
        "Mantiene separados con loops limpios los Face Sets del sculpt",
    "Keep the masked area separated from the rest by a clean edge loop":
        "Mantiene el área enmascarada separada del resto con un loop limpio",
    "Mask value above which a face counts as masked":
        "Valor de máscara a partir del cual una cara cuenta como enmascarada",
    "Mirror on the local X axis": "Espejo en el eje X local",
    "Mirror on the local Y axis": "Espejo en el eje Y local",
    "Mirror on the local Z axis": "Espejo en el eje Z local",
    "Which half of the model is remeshed and mirrored": "Qué mitad del modelo se remalla y se espeja",
    "Keep the half with more surface area": "Conserva la mitad con más superficie",
    "Keep the positive side of the axis": "Conserva el lado positivo del eje",
    "Keep the negative side of the axis": "Conserva el lado negativo del eje",
    "Where the mirror plane sits": "Dónde está el plano de espejo",
    "The plane passes through the object origin": "El plano pasa por el origen del objeto",
    "The plane passes through the middle of the model": "El plano pasa por el centro del modelo",
    "How close to the plane counts as 'on the plane', relative to the model size":
        "Qué tan cerca del plano cuenta como 'sobre el plano', relativo al tamaño del modelo",
    "Check the model and warn if it is not really symmetric":
        "Revisa el modelo y avisa si no es realmente simétrico",
    "Merge duplicate vertices and remove loose geometry before remeshing":
        "Une vértices duplicados y elimina geometría suelta antes de remallar",
    "Hide the original object after remeshing (it is never deleted)":
        "Oculta el objeto original tras remallar (nunca se borra)",
    "Copy the UV maps of the original onto the result. Faces next to an old UV seam are only approximate; bake from the original or unwrap again for final texturing":
        "Copia los mapas UV del original al resultado. Las caras junto a una costura UV antigua son aproximadas; para texturizar al final, hornea desde el original o desenvuelve de nuevo",
    "Copy the sculpt Face Sets (polygroups) and the sculpt mask of the original onto the result, so it goes straight back into Sculpt Mode with the same groups":
        "Copia los Face Sets (polygrupos) y la máscara de escultura del original al resultado, para volver a esculpir con los mismos grupos",
    "Random seed of the engine. Change it to get a different edge flow":
        "Semilla aleatoria del motor. Cámbiala para obtener otro flujo de aristas",
}


def _build_table() -> dict:
    """Entries for the 'default' context, plus the 'Operator' context used by button labels."""
    table = {}
    for english, spanish in {**SPANISH_FROM_TEXTS, **SPANISH_INTERFACE}.items():
        table[("*", english)] = spanish
        table[("Operator", english)] = spanish
    return table


def register_translations() -> None:
    """Never let a translation problem stop the add-on from loading."""
    try:
        available = set(bpy.app.translations.locales)
        table = _build_table()
        dictionary = {locale: table for locale in ("es", "es_ES") if locale in available}
        if dictionary:
            bpy.app.translations.register(__name__, dictionary)
    except Exception as error:  # noqa: BLE001
        print(f"QuadForge: translations not loaded ({error!r})")


def unregister_translations() -> None:
    try:
        bpy.app.translations.unregister(__name__)
    except Exception:  # noqa: BLE001
        pass
