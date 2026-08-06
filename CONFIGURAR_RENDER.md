# Linkflix — salvar filmes permanentemente no Render

O projeto já está preparado para usar PostgreSQL quando a variável `DATABASE_URL` existir.
A importação pelo TMDB continua exatamente igual.

## 1. Atualizar o mesmo repositório do GitHub

Envie os arquivos desta pasta para o mesmo repositório que o Render já usa.
Não crie outro repositório.

## 2. Criar o banco no Render

1. No painel do Render, clique em **New +** → **PostgreSQL**.
2. Crie o banco na mesma região do serviço Linkflix.
3. Abra o banco e copie a **Internal Database URL**.
4. Abra o serviço web do Linkflix → **Environment**.
5. Adicione a variável `DATABASE_URL` com a URL copiada.
6. Salve. O Render fará um novo deploy.

## 3. Migração automática

No primeiro acesso após configurar `DATABASE_URL`, o site cria as tabelas e, se o PostgreSQL estiver vazio, copia automaticamente os dados do arquivo `instance/linkflix.db` incluído no projeto.

Depois disso, novos filmes, usuários, perfis, favoritos e progresso ficam no PostgreSQL e não somem quando o Render reiniciar.

## Tela cheia

A página de reprodução agora tem um botão próprio **Tela cheia** sobre o player. Ele coloca o bloco inteiro do player em tela cheia mesmo quando o player externo não mostra o botão nativo.
