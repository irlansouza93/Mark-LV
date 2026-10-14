# Avatares 3D para o HUD

Coloque aqui um modelo em **.obj** ou **.glb** e ele aparece em
⚙ SETUP → CUSTOMISE ASSISTANT → HUD STYLE com o nome do arquivo, renderizado
como holograma (luz, bloom, lip-sync pelo queixo).

O importador (`core/mesh_import.py`) prepara o modelo sozinho:

- **Isola a cabeça**: corpo inteiro / T-pose é cortado nos ombros; bustos
  ficam com só um "colarinho" abaixo do queixo.
- **Vira para a frente**: pelas malhas de olho (`Eye…`), ou, sem elas, pelo
  lado em que o queixo fica à frente do pescoço.
- **Usa a textura** (base color do GLB, `map_Kd` do OBJ) como brilho: é o que
  devolve olhos, sobrancelhas, lábios e mechas. Cabelo em "cards" com alfa
  transparente não vira placa sólida.
- **Rigs (skin) do GLB** são posados pelo esqueleto.
- A primeira carga de um arquivo grande leva segundos (130 MB ≈ 8 s, numa
  thread — o HUD mostra o reator enquanto isso); depois fica em cache em
  `config/avatar_cache/` (~20 ms).

## Aparência e anatomia: ⚙ SETUP → ✨ HOLOGRAMA

O painel ajusta **ao vivo** o holograma do modelo que está no HUD e salva por
modelo (`config/holo_looks.json`):

- **Render**: *Holograma* (luz, efeitos) ou *Realista (personagem de jogo)* —
  o modelo iluminado de verdade (luz principal, preenchimento, contorno,
  especular), opaco, com antisserrilhado, sem ruído/faixas/glitch. Os olhos
  giram com o olhar (modelos com globo ocular), as pálpebras fecham na cor da
  pele, a boca abre entre os lábios mostrando o interior e os dentes, e os
  lábios esticam/arredondam com as vogais.
- **Presets**: Realista, Clássico (partículas), Jogo (superfície), Cor
  original, Misto, Fantasma — e os seus (`＋ salvar o atual como preset…`; um preset leva o
  visual, não a anatomia).
- **Estilo**: superfície (a malha real na GPU), partículas, linhas.
- **Cor**: cor original da textura ↔ cor do tema, detalhe e contraste da
  textura, preenchimento, borda (fresnel), translucidez.
- **Efeitos**: faixas de varredura, onda de energia, ruído digital,
  cintilação, glitch, linhas de tela, bloom, exposição, aberração, base.
- **Olhos / Boca**: estilo dos olhos (brilho, íris, só a textura, nenhum),
  piscar, olhar, abertura da mandíbula, brilho da boca — e a **calibração**:
  posição e abertura dos olhos (a elipse dos Marcadores deve cobrir o olho —
  é ela que a pálpebra fecha), linha da boca (a costura entre os lábios),
  largura da boca, queixo e pivô da mandíbula. Ligue **Marcadores** para ver
  onde estão.
- **↺ PADRÃO** volta ao automático (anatomia detectada no modelo).

## Boca e olhos: como cada modelo é animado

1. **Blendshapes do próprio modelo** (o padrão dos jogos) — se o GLB tem morph
   targets (`jawOpen`, `eyeBlinkLeft/Right`, `viseme_aa`, VRM `Fcl_MTH_A`…), a
   fala e o piscar usam as formas que o artista esculpiu. Avatares do Ready
   Player Me (baixe com `?morphTargets=ARKit,Oculus Visemes`) e VRoid/VRM
   trazem isso. No Blender, exporte com "Shape Keys" marcado.
2. **Landmarks (MediaPipe)** — sem blendshapes, o app renderiza o rosto de
   frente uma vez, acha os 478 pontos do rosto e corta a malha exatamente
   na linha entre os lábios (com interior da boca e dentes), fecha as
   pálpebras pelo contorno real dos olhos e move a íris de olhos pintados.
   Precisa de `pip install mediapipe` e do arquivo
   `assets/models/face_landmarker.task` (link em `requirements.txt`).
3. **Sem rosto humano** (máscara, robô) — nada de boca ou pálpebra falsa:
   só um leve movimento do queixo.

O painel ✨ HOLOGRAMA mostra qual dos três o modelo está usando; a
calibração manual de olhos/boca só aparece no caso 3.

## Pré-processamento (raramente necessário)

Ajuste fino opcional em `<nome>.json` ao lado do modelo (só se o automático
errar na rotação ou no corte):

```json
{ "rotate": [0, 90, 0], "crop": 0.4, "head_frac": 0.78, "mouth": 0.62,
  "eyes": [[-0.3, 0.05, 0.6], [0.3, 0.05, 0.6]], "texture": 1.0 }
```

`rotate` = graus em x, y, z (desliga a orientação automática); `crop` = manter
só a fração de cima do modelo; `head_frac` = fração de cima (do que sobrou)
que é a cabeça; `mouth` = altura da boca de 0 (topo) a 1 (queixo); `eyes` =
posição dos olhos em unidades normalizadas (topo = +1, queixo = -1), ou
`false` para nenhum brilho; `texture` = 0 ignora a textura.

Imagens (.png) não são modelos 3D e são ignoradas. Arquivos começando com `_`
também.
