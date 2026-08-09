# Linkflix V37 - fallback iOS/Safari

Esta versão muda o fluxo do player no iPhone/iPad:

- O botão principal do iOS abre o player externo diretamente em uma nova aba/navegador, sem depender primeiro do iframe.
- O clique é um link real (`<a target="_blank">`) para preservar o gesto do usuário e aumentar a compatibilidade com Safari/WebView/PWA.
- Filmes recebem a URL direta automaticamente.
- Séries atualizam a URL direta conforme temporada/episódio selecionado.
- Continua existindo **Tentar aqui** como fallback para carregar o iframe dentro do Linkflix.
- Se o iframe continuar sem reproduzir, aparece **Abrir novamente no Safari**.
- O botão de tela cheia fica oculto no mobile iOS para evitar um fluxo que não ajuda quando o player precisa sair do WebView.
- PC e outros dispositivos mantêm o comportamento anterior.

Observação: se o provedor externo do player em si não suportar Safari/iOS, o Linkflix não consegue converter o stream. Nesse caso é necessário trocar a fonte/player por uma compatível com iOS.
