"""
Human Behavior Simulator — Simulacao de comportamento humano para automacao web.

Copiado de: PUBLICIDADE-MODULO/skills/browser-automation-ml/scripts/human_behavior.py
Sem alteracoes — 100% funcional.
"""

import asyncio
import math
import random


class HumanBehavior:
    """Simula comportamento humano natural em paginas web."""

    def __init__(self, page):
        self.page = page
        self._mouse_x = 0
        self._mouse_y = 0

    # --- Mouse ---

    async def mover_mouse(self, x: int, y: int):
        """Move mouse com curva bezier natural (nao linha reta)."""
        start_x, start_y = self._mouse_x, self._mouse_y
        steps = random.randint(20, 40)

        ctrl_x = (start_x + x) / 2 + random.uniform(-100, 100)
        ctrl_y = (start_y + y) / 2 + random.uniform(-50, 50)

        for i in range(steps + 1):
            t = i / steps
            bx = (1 - t) ** 2 * start_x + 2 * (1 - t) * t * ctrl_x + t ** 2 * x
            by = (1 - t) ** 2 * start_y + 2 * (1 - t) * t * ctrl_y + t ** 2 * y

            bx += random.uniform(-1.5, 1.5)
            by += random.uniform(-1.5, 1.5)

            await self.page.mouse.move(bx, by)

            speed = 0.005 + 0.015 * math.sin(t * math.pi)
            await asyncio.sleep(speed + random.uniform(0, 0.005))

        self._mouse_x = x
        self._mouse_y = y

    async def clicar(self, selector: str):
        """Clica em elemento com movimento natural do mouse."""
        element = await self.page.wait_for_selector(selector, timeout=10000)
        box = await element.bounding_box()
        if box:
            target_x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
            target_y = box["y"] + box["height"] * random.uniform(0.3, 0.7)
            await self.mover_mouse(target_x, target_y)
            await asyncio.sleep(random.uniform(0.05, 0.15))
            await self.page.mouse.click(target_x, target_y)
        else:
            await element.click()

    async def clicar_elemento(self, element):
        """Clica em elemento ja localizado."""
        box = await element.bounding_box()
        if box:
            target_x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
            target_y = box["y"] + box["height"] * random.uniform(0.3, 0.7)
            await self.mover_mouse(target_x, target_y)
            await asyncio.sleep(random.uniform(0.05, 0.15))
            await self.page.mouse.click(target_x, target_y)
        else:
            await element.click()

    # --- Scroll ---

    async def scroll(self, pixels: int, direcao: str = "baixo"):
        """Scroll gradual com velocidade variavel."""
        scrolled = 0
        multiplicador = 1 if direcao == "baixo" else -1

        while scrolled < abs(pixels):
            delta = random.randint(30, 120) * multiplicador
            await self.page.mouse.wheel(0, delta)
            scrolled += abs(delta)

            await asyncio.sleep(random.uniform(0.03, 0.12))

            if random.random() < 0.1:
                await asyncio.sleep(random.uniform(0.3, 1.0))

    async def scroll_ate_elemento(self, selector: str):
        """Scroll gradual ate um elemento ficar visivel."""
        for _ in range(20):
            try:
                visible = await self.page.is_visible(selector)
                if visible:
                    return True
            except Exception:
                pass
            await self.scroll(random.randint(200, 400))
            await asyncio.sleep(random.uniform(0.3, 0.8))
        return False

    # --- Digitacao ---

    async def digitar(self, selector: str, texto: str):
        """Digita com velocidade humana variavel."""
        await self.clicar(selector)
        await asyncio.sleep(random.uniform(0.2, 0.5))

        for i, char in enumerate(texto):
            await self.page.keyboard.type(char, delay=random.randint(40, 180))

            if char in "@.-_ " or (i > 0 and i % random.randint(4, 8) == 0):
                await asyncio.sleep(random.uniform(0.1, 0.4))

            if random.random() < 0.03:
                await asyncio.sleep(random.uniform(0.5, 1.5))

    async def limpar_e_digitar(self, selector: str, texto: str):
        """Seleciona todo o texto existente e digita novo valor."""
        await self.clicar(selector)
        await asyncio.sleep(random.uniform(0.1, 0.3))
        await self.page.keyboard.press("Control+a")
        await asyncio.sleep(random.uniform(0.1, 0.2))
        await self.page.keyboard.press("Backspace")
        await asyncio.sleep(random.uniform(0.2, 0.5))
        await self.digitar(selector, texto)

    async def limpar_e_digitar_elemento(self, element, texto: str):
        """Limpa e digita em elemento ja localizado."""
        await self.clicar_elemento(element)
        await asyncio.sleep(random.uniform(0.1, 0.3))
        await self.page.keyboard.press("Control+a")
        await asyncio.sleep(random.uniform(0.1, 0.2))
        await self.page.keyboard.press("Backspace")
        await asyncio.sleep(random.uniform(0.2, 0.5))

        for i, char in enumerate(texto):
            await self.page.keyboard.type(char, delay=random.randint(40, 180))
            if char in ".,- " or (i > 0 and i % random.randint(4, 8) == 0):
                await asyncio.sleep(random.uniform(0.05, 0.2))

    # --- Pausas ---

    async def pausa(self, min_s: float = 0.5, max_s: float = 2.5):
        """Pausa com duracao aleatoria."""
        await asyncio.sleep(random.uniform(min_s, max_s))

    async def pausa_leitura(self, num_palavras: int = 50):
        """Pausa proporcional ao conteudo."""
        tempo = (num_palavras / 250) * 60
        tempo *= random.uniform(0.7, 1.3)
        await asyncio.sleep(min(tempo, 10))

    # --- Navegacao ---

    async def navegar_naturalmente(self, url: str):
        """Navega para URL com comportamento humano."""
        if random.random() < 0.3:
            await self.mover_mouse(
                random.randint(100, 800),
                random.randint(100, 500),
            )

        await self.page.goto(url)
        await self.page.wait_for_load_state("networkidle")
        await self.pausa(1, 3)

        if random.random() < 0.5:
            await self.scroll(random.randint(100, 300))

    async def simular_atividade(self, duracao_segundos: int = 30):
        """Simula atividade generica na pagina."""
        inicio = asyncio.get_event_loop().time()

        while asyncio.get_event_loop().time() - inicio < duracao_segundos:
            acao = random.choice(["scroll", "mover", "pausa"])

            if acao == "scroll":
                direcao = random.choice(["baixo", "cima"])
                await self.scroll(random.randint(50, 200), direcao)
            elif acao == "mover":
                await self.mover_mouse(
                    random.randint(100, 1200),
                    random.randint(100, 600),
                )
            else:
                await self.pausa(1, 4)

            await asyncio.sleep(random.uniform(0.5, 2))
