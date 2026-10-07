# Changelog

## 0.3.2

- Desenha spots embutidos junto aos degraus da entrada, com aro escuro e luz quente.
- Os spots acompanham o estado de Luz da Escada e acionam o mesmo canal ao clicar.
- O cabo do canal Luz da Escada termina na primeira lampada da sequencia.

## 0.1.5

- Declara `init: false`, exigido pela base s6 v3 do Home Assistant.

## 0.1.4

- Remove wrapper `with-contenv` do serviço para evitar falha de inicialização no Supervisor.

## 0.1.3

- Ajusta inicialização para o formato de serviço s6 exigido pela base do Home Assistant.

## 0.1.2

- Remove mapeamento de porta externa; o acesso passa a ser somente via Ingress.

## 0.1.1

- Corrige build local usando a imagem base do Home Assistant.

## 0.1.0

- Versão inicial do add-on Casa 3D.
- Painel via Ingress no Home Assistant.
- Base para editor de posições e futuras visões da casa.
