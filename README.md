# Monovia 0.3 — instalação em quatro arquivos

Esta entrega muda apenas a embalagem e a inicialização do aplicativo já entregue.
Fórmulas, critérios, hipóteses, escopo e arquivos internos permanecem iguais à fonte
oficial `LRO_MONOVIA_030_20261018.zip`.

## Instalar e iniciar

1. Extraia **somente o ZIP externo desta entrega** para uma pasta própria.
2. Mantenha juntos os quatro arquivos: `app.py`, `requirements.txt`,
   `codigo_lro.zip` e `README.md`.
3. **NÃO extraia `codigo_lro.zip`.** O bootstrap cuida da extração e da integridade.
4. Use Python 3.12 de 64 bits. Abra um terminal nessa pasta e crie um ambiente virtual:

```text
python -m venv .venv
```

No Windows:

```text
.venv\Scripts\activate
python -m pip install -r requirements.txt
streamlit run app.py
```

No Linux/macOS:

```text
source .venv/bin/activate
python -m pip install -r requirements.txt
streamlit run app.py
```

Se o comando `streamlit` não estiver no PATH do ambiente ativado, use:

```text
python -m streamlit run app.py
```

O aplicativo abre no navegador. A navegação apresenta **NBR inicial**,
**Complemento EN** e **Fadiga NBR**. Os dois links da página inicial também levam
às respectivas rotas. As páginas mantêm seus editores, exemplos, cálculos,
figuras e downloads Word/JSON originais.

## Arquivos internos, documentação e exemplos

O ZIP interno é uma cópia byte a byte do pacote oficial, com 290 entradas:
289 arquivos listados no manifesto mais `RUNTIME_MANIFEST.json`.
Todos os arquivos são extraídos fisicamente para o runtime; não se usa importação
direta de dentro do ZIP.

Na primeira inicialização é criada a pasta técnica `.monovia_runtime` ao lado dos
quatro arquivos. Ela contém o cache e uma cópia de execução exclusiva por processo:

```text
.monovia_runtime/
  cache/<SHA256 completo>/LRO_MONOVIA_030_20261018/
  runs/<SHA256 completo>-<identificador>/LRO_MONOVIA_030_20261018/
```

Dentro dessas árvores estão o `README.md` original, `GUIA_RAPIDO.docx`, `docs/`,
`examples/`, páginas, catálogo, figuras, código e demais recursos originais.
Consulte `examples/README.md` para os exemplos de cada rota e os limites de
interpretação. Os exemplos também podem ser importados pelos editores JSON
originais; os botões de exemplo das páginas continuam disponíveis.

O nome externo `app.py` é apenas o bootstrap. O `app.py` interno continua no seu
diretório original, com caminho absoluto distinto; não há chamada recursiva ao
bootstrap. A adaptação dos dois links da página inicial ocorre apenas no namespace
de execução da interface, sem regravar qualquer arquivo do pacote.

## Integridade, atualização e isolamento

Fonte oficial: `LRO_MONOVIA_030_20261018.zip`, 4.248.609 bytes.
SHA256 do arquivo original e de `codigo_lro.zip`:

```text
68de90199398c98c947c2358b7e36a1a29ec4bbdbe383b7cb483e228abce462a
```

O bootstrap valida os bytes do ZIP antes de extrair. Os caminhos são validados;
travessias, nomes Windows especiais, duplicatas, colisões, links e entradas
especiais são recusados. A extração é publicada a partir de uma pasta temporária
verificada. O cache usa o SHA256 completo, não apenas o nome ou a data do arquivo.
Na reutilização, todos os arquivos e o recibo de cache são conferidos. Um cache
incompleto ou alterado é isolado e uma nova extração íntegra é criada.

Cada processo recebe sua própria cópia física de execução. O diretório de trabalho
e os imports apontam para essa árvore, preservando leituras relativas e recibos
baseados nas fontes físicas. Cada nova execução do script Streamlit confere
novamente o ZIP e os arquivos do runtime antes de executar a página.

**Encerre o servidor com Ctrl+C antes de substituir qualquer arquivo da entrega.**
Se o ZIP mudar durante um processo ativo, o aplicativo bloqueia a continuação e
exige reinício; módulos antigos não são combinados com fontes novas. Esta versão
aceita apenas o hash oficial acima. Uma versão futura exige sua própria entrega
coerente, incluindo o bootstrap correspondente. Não se deve misturar arquivos
de entregas diferentes.

O isolamento é por processo de servidor; sessões do mesmo servidor usam o mesmo
código imutável e mantêm o estado de projeto nos namespaces de sessão originais.
Os relatórios Word são gerados em memória e disponibilizados para download; o
aplicativo não controla nem abre o Microsoft Word. A pasta deve permitir gravação
do runtime. Para liberar espaço, encerre todos os processos desta instalação
antes de remover manualmente `.monovia_runtime`; ela será recriada.

## Escopo preservado

As três rotas e seus recibos continuam independentes. Exemplos ilustrativos e
status parciais não aprovam a seção inteira, a instalação ou o equipamento.
Valem integralmente as ressalvas, pendências e limitações da documentação original.
Não houve integração com Robot nem publicação GitHub/Streamlit.

## Verificação desta embalagem

O relatório da entrega registra os testes efetivamente executados e suas
limitações. Os testes da embalagem não substituem a auditoria de engenharia
descrita na documentação original.
