# Linkflix V36 - compatibilidade iOS

Alterações no player:
- Detecta iPhone/iPad e evita o modo cinema automático, que pode causar tela preta em Safari/WKWebView.
- No iOS o iframe só é carregado depois de um toque real em “Iniciar filme/episódio”.
- Mantém o comportamento automático existente no PC/Android.
- Adiciona fallback “Abrir player compatível” para navegar diretamente ao player externo caso o iframe continue preto.
- Adiciona safe-area e 100dvh para iPhones com notch/ilha dinâmica.
- Permite recursos de apresentação/fullscreen e popups exigidos por alguns players externos.
- Séries seguem a mesma regra de toque no iOS a cada episódio.

Observação: como o vídeo é fornecido por um player externo, o Linkflix não controla codec, HLS, DRM ou suporte final do provedor a Safari. O fallback direto maximiza a chance de reprodução no iOS.
