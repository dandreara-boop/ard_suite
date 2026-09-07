class CodigoBarraService:
    def normalize_manual_code(self, codigo_barra: str) -> str:
        return codigo_barra.strip()

    def generate_for_variant(self, codigo_articulo: str, codigos_valores: list[str]) -> str:
        return f"{codigo_articulo}{''.join(codigos_valores)}"
