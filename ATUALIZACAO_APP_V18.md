# Linkflix V18 — abertura e perfis

- Usuário novo: abre Login.
- Usuário já autenticado: não pede login novamente; abre a splash e depois Quem está assistindo?.
- Ao escolher um perfil, entra na Home.
- Ao fechar e abrir o app/site em uma nova sessão, pede o perfil novamente.
- Manifesto, ícones e service worker foram versionados para eliminar o cache antigo.

## Importante no celular
A tela preta com apenas o L é a splash nativa do PWA/app instalado, gerada pelo Android a partir do ícone antigo. Depois do deploy, feche totalmente o app e abra novamente. Se o Android continuar mostrando o ícone antigo, remova somente o atalho/app instalado e instale novamente uma vez. Seus dados, filmes e contas ficam no PostgreSQL e não serão apagados.
