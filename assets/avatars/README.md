# Avatares 3D para o HUD

Coloque aqui um modelo de cabeça em **.obj** ou **.glb** e ele aparece em
⚙ SETUP → CUSTOMISE ASSISTANT → HUD STYLE com o nome do arquivo, renderizado
como holograma (luz, bloom, lip-sync pelo queixo).

- Melhor resultado: busto/cabeça, olhando para +Z (de frente), Y para cima,
  20 mil a 200 mil triângulos. Texturas são ignoradas — o holograma é luz.
- Ajuste fino opcional em `<nome>.json` ao lado do modelo:

```json
{ "rotate_y": 180, "head_frac": 0.78, "mouth": 0.62,
  "eyes": [[-0.3, 0.05, 0.6], [0.3, 0.05, 0.6]] }
```

`head_frac` = fração superior do modelo que é a cabeça (o resto, ombros);
`mouth` = altura da boca de 0 (topo) a 1 (queixo); `eyes` = posição dos olhos
em unidades normalizadas (topo = +1, queixo = -1), para o brilho das íris.

Arquivos começando com `_` são ignorados.
