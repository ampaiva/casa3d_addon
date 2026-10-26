# Casa 3D

Aplicativo web para evoluir a planta real da casa dentro do Home Assistant.

Esta primeira versão empacota o editor de posições que já está funcionando localmente:

- posicionamento físico de switches/dispositivos;
- posicionamento separado das cargas/lâmpadas;
- miniaturas Girier 1, 2, 3 e 4 canais;
- disjuntor Zigbee do aquecedor;
- fios entre canais e cargas;
- base para novas visões: Zigbee, elétrica e hidráulica.

## Rodar localmente

```bash
python -m app.position_editor_server --host 127.0.0.1 --port 8765
```

Depois abra `http://127.0.0.1:8765`.

## Imagem da planta

A imagem do térreo é configuração/dado do app, não deve ser inferida pelo código.
Nesta versão inicial ela aponta para:

```text
assets/casa_3d_terreo-v18.webp
```

Quando a planta mudar para `v19`, `v20` ou outra versão, a troca deve acontecer na configuração do app e ser preservada em qualquer deploy.


## Eletrodutos do projeto

No seletor de visões, **Eletrodutos · Térreo** e **Eletrodutos · Superior**
mostram a referência elétrica extraída do DWG sobre a planta 3D. Na barra da
planta normal, o botão **Eletrodutos de piso e teto** abre a sobreposição do
pavimento atual. O seletor **Base** alterna entre a imagem 3D e a planta técnica. Piso usa pontilhado;
teto/laje usa traço contínuo, conforme a convenção aceita para este projeto.
Os filtros são independentes. Selecionar uma caixa destaca apenas os trechos
que chegam ao símbolo da caixa, sem criar conexões em cruzamentos de linhas.

Há três caixas identificadas no térreo e uma no superior (150 × 150 × 80 mm).
Os identificadores CP-T01…CP-S01 são de consulta, atribuídos na importação.
Os quadros reais continuam usando as posições e os circuitos já cadastrados
em `infrastructure.panels`, no depósito. Nenhuma ligação do DWG é atribuída
a esses quadros automaticamente.

O alinhamento visual sobre as imagens 3D usa pontos de referência das paredes,
escada, piscina e limites do terreno, com transformação afim por triângulos.
Ele é aproximado: não comprova o caminho executado, a profundidade, a altura
ou as cargas reais. As curvas do desenho são preservadas como referência, sem
criar ligações até os quadros reais no depósito. Os quadros aparecem nas
coordenadas já salvas no cadastro, separadas da geometria importada.

A calibração fica em `assets/conduits-alignment.json`, associada ao nome e às
dimensões da imagem de cada pavimento (térreo v18 e v20; superior original, incluindo o nome `casa_3d_superior.webp` usado na instalação). Uma imagem configurada com outro nome
é preservada; nesse caso, o app mostra a planta técnica até que seja alinhada.
Revisões que substituam a imagem mantendo o mesmo nome também exigem revisar
a calibração. Para abrir diretamente a prévia, use
`/?view=eletrodutos-terreo` ou `/?view=eletrodutos-superior`.

### Atualizar a referência

Converter o DWG localmente para DXF (por exemplo, com LibreDWG) e executar,
em um ambiente separado com `ezdxf` instalado:

```bash
python scripts/import_electrical_dxf.py /caminho/projeto.dxf
```

O importador é específico para as duas plantas e a simbologia deste projeto.
Ele gera `assets/conduits.json` e as duas bases SVG. Não incluir o DWG/DXF
original no repositório. O aplicativo serve apenas os dados já extraídos e
não precisa de bibliotecas CAD durante a execução.

## Cômodos e localização pelo Home Assistant

Abra **Cômodos** na barra da Casa 3D. Associe cada andar do HA à planta
Térreo ou Superior. Marque os cômodos que deseja vincular e informe sua região
na imagem: use **Marcar na planta** e clique em dois cantos opostos do cômodo,
ou informe esquerda, topo, largura e altura em porcentagens. Esc cancela a marcação. A planta do
cômodo pode herdar o andar do HA ou ser escolhida explicitamente. Salve os vínculos.
No modo **Editar posições**, as regiões aparecem tracejadas sobre a planta.

Os vínculos usam os identificadores do HA; renomear um cômodo não rompe o vínculo.
Dispositivos novos aparecem no centro da região, com posição a confirmar.
Arraste o ícone e salve para fixar a posição exata. Se o cômodo do dispositivo
mudar no HA, o ícone passa para o novo cômodo e volta a exigir ajuste da posição.
Uma área específica da entidade tem precedência sobre a área do dispositivo.

A descoberta consulta os cadastros locais do HA ao abrir a página e a cada
30 segundos enquanto não há edição em andamento. Não altera o cadastro do HA.
Dispositivos sem cômodo ou com cômodo sem vínculo aparecem em **Cômodos → A localizar**.
As posições manuais anteriores à criação dos vínculos são preservadas até que
o cômodo correspondente seja vinculado. Lâmpadas/cargas mantêm suas posições
independentes dos módulos físicos. A descoberta inclui dispositivos com entidades ativas, inclusive eletrodomésticos
expostos somente por sensores; entidades inativas, de configuração e de diagnóstico
não geram novos ícones.

O mapeamento e as posições ficam em `casa3d-controls.json`, no diretório de
dados persistentes, sem modificar as imagens ou o banco de dados do HA.

## Descoberta de dispositivos e energia

A descoberta parte dos dispositivos do HA com ao menos uma entidade ativa,
visível e fora das categorias de configuração/diagnóstico. Não depende do tipo
de entidade: um eletrodoméstico exposto somente por sensores também ganha um
único ícone. Os novos ícones genéricos recebem um ponto estável dentro da região
do cômodo. O identificador do dispositivo preserva sua posição ao renomear uma
entidade. Dispositivos automáticos removidos ou desativados deixam de aparecer.

A visão **Elétrica → Energia dos dispositivos** inclui automaticamente entidades
com classes energy, power, current e voltage, agrupadas pelo dispositivo e com
leituras ao vivo do HA. Os valores permanecem separados por entidade; não são
somados, pois medidores podem representar totais, tarifas ou intervalos diferentes.

Quadro, circuito e fase são recuperados de vínculos elétricos existentes da Casa
3D ou de uma leitura cadastrada em um circuito que pertença ao mesmo dispositivo.
A origem do vínculo aparece na descrição. Relações conflitantes ou ausentes são
mostradas como pendentes. Não se infere fase pela tensão ou cômodo, nem
se atribui o circuito de um gateway a todos os dispositivos conectados a ele.
As cargas/lâmpadas mantêm suas posições independentes. A descoberta alimenta a visão de energia da Casa 3D. O cadastro no dashboard
Energia do HA é feito separadamente pela sincronização automática descrita abaixo.

## Sincronização Tuya → Home Assistant (0.7.0)

O complemento usa a conta cloud LocalTuya já configurada e o acesso autorizado
à API do HA (`homeassistant_api: true`). A cada 15 minutos, mesmo sem navegador
aberto, consulta os cômodos Tuya e associa dispositivos com área vazia no HA.
Os vínculos usam IDs de casa/cômodo e de dispositivos, incluindo representações
Tuya e LocalTuya. Nomes iguais são comparados sem acentos, espaços extras e
preposições; “Sala de Estar” corresponde a “Sala”. Nomes ambíguos ficam pendentes.
Cômodos ainda inexistentes são criados no HA, com andar herdado apenas quando
os dispositivos já localizados evidenciam um único andar. Ainda é necessário
marcar a região de cada cômodo novo na planta uma vez.

Uma associação manual preexistente no HA tem prioridade. Dispositivos que a
sincronização associou podem acompanhar mudanças de cômodo no Tuya; se sua
área for alterada ou limpa manualmente no HA, a sincronização para de movê-los.
Em **Cômodos → Sincronização de cômodos Tuya → HA**, é possível escolher uma
área correspondente diferente ou ignorar um cômodo. Isso não sobrescreve áreas
manuais. As alterações entram no próximo ciclo (até 15 minutos).

Os registros do HA são atualizados pela API oficial, nunca editando `.storage`.
Credenciais ficam na instalação e não são enviadas à interface. A configuração
`tuya_room_sync: false` desativa novas sincronizações, preservando os vínculos
já feitos. O estado de propriedade dos vínculos fica em `/data/tuya-room-sync.json`;
não o apague para evitar perder a distinção entre áreas automáticas e manuais.

### Correção de tensão e potência da tomada 16A Estimate Socket

`tuya_voltage_fix` instala em `/config/tuya_quirks/casa3d_voltage.py` um ajuste
persistente para o produto `vSu2uIvNamkj9gdG`: o datapoint 6 (`cur_voltage`) usa
escala 1, convertendo 1250 em 125 V; o datapoint 5 (`cur_power`) também usa
escala 1, convertendo 8020 em 802 W. Usa o carregador de quirks do Tuya disponível
na versão do HA instalada e recarrega somente a integração Tuya quando o arquivo
muda. Não altera o núcleo do HA nem o histórico já gravado. A comparação passiva
normaliza por 10 somente a potência histórica desse produto anterior à ativação
do ajuste, registrada em `/data/voltage-fix.json`. Leituras posteriores usam a
escala corrigida do HA; os registros originais do Recorder são preservados.
Desativar a opção interrompe novas instalações; para reverter a correção ativa,
remova o arquivo de quirk e recarregue a integração Tuya. Isso restaura a leitura
original sem transformar dados históricos.

### Identificação passiva da alimentação

`energy_learning` compara até 24 horas de potência das cargas com os canais A/B/C
dos medidores. Normaliza W/kW, ignora valores inválidos e exige pelo menos seis
variações coincidentes, incluindo duas subidas e duas descidas, concordância de
80% e vantagem mínima de 25 pontos percentuais sobre o segundo candidato.
Sinais iguais em dois medidores são considerados ambíguos. Não aciona aparelhos.

O resultado aparece na visão Elétrica como **alimentação provável**, com medidor,
canal e evidência. Não vira um circuito cadastrado: um medidor de quadro não
identifica o disjuntor individual. Canal A/B/C também não é tratado como L1/L2/L3
sem confirmação da ligação física. Vínculos elétricos explícitos continuam tendo
prioridade. Falhas ou resultados antigos deixam de apresentar candidatos atuais.

### Referências e catálogo elétrico (0.7.3)

Os canais das entidades **Fase A/B/C** nos cartões **Quadro ...** do painel
elétrico definem as referências da comparação. Entidades antigas fora desses
cartões não participam; uma referência cadastrada sem leitura continua sendo
um impedimento, nunca é descartada para favorecer um resultado. Alternativas
só substituem a referência quando pertencem ao mesmo dispositivo físico.
O painel mostra quantos canais estão ativos e o motivo de cada pendência.

A tabela **Circuitos catalogados** e os cartões de energia usam os mesmos vínculos.
As linhas existentes são associadas a IDs de dispositivos sem adivinhar nomes
ambíguos, preservando a origem, a confiança e as observações históricas. Os IDs
ficam em `/data/electrical-catalog-bindings.json`, resistindo a renomeações.
Esses vínculos não inventam disjuntores nem equivalências entre A/B/C e L1/L2/L3;
cadastros explícitos de circuito têm prioridade. Uma identificação por comparação
continua aparecendo como provável, sem se transformar em confirmação física.

## Cadastro automático no dashboard Energia do HA (0.8.0)

`energy_dashboard_sync` inclui novos dispositivos em **Energia → Eletricidade →
Dispositivos individuais**, pela API `energy/save_prefs`, a cada 15 minutos.
Independe da planta, do cômodo e da identificação do quadro/fase. Requer um único
contador de energia por dispositivo físico, unidade Wh/kWh/MWh, classe `energy`,
estado acumulativo válido e estatísticas com soma disponíveis no Recorder.
Dispositivos recém-descobertos aguardam estatísticas compatíveis no próximo ciclo.

Preserva todas as seleções existentes, nomes, hierarquias, fontes de rede/solar
e água. Não inclui medidores de quadro, produção, economia ou contadores ambíguos;
nem duplica um dispositivo que já tenha outra entidade selecionada, inclusive
representações Tuya/LocalTuya com a mesma identidade física. Dispositivos somente
com potência não recebem consumo estimado automaticamente.

Antes de cada alteração, salva as preferências originais em
`/data/energy-prefs-before-*.json`. O estado fica em `/data/energy-dashboard-sync.json`.
Se o usuário remover um dispositivo incluído pela sincronização nas configurações
de Energia, sua exclusão é respeitada. `energy_dashboard_sync: false` desativa novas
inclusões. Não altera o histórico e não remove cadastros quando uma leitura fica
indisponível. Alterações concorrentes detectadas adiam a gravação para o próximo ciclo.
