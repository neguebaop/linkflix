# LINKFLIX — SALVAR FILMES PARA NÃO SUMIREM NO RENDER

Esta versão usa **PostgreSQL** quando a variável `DATABASE_URL` está configurada.
Depois disso, filmes, séries, desenhos, categorias, usuários, perfis, favoritos e progresso ficam fora do disco temporário do serviço web e continuam existindo após deploys e reinícios.

## PARTE 1 — enviar esta versão para o mesmo GitHub

1. Extraia este ZIP no computador.
2. Abra o repositório atual do Linkflix no GitHub.
3. Substitua os arquivos antigos pelos arquivos desta versão.
4. Faça commit no mesmo branch usado pelo Render, normalmente `main`.
5. Aguarde o deploy automático terminar.

Não crie outro repositório.

## PARTE 2 — criar o PostgreSQL no Render

1. Entre no painel do Render.
2. Clique em **New +**.
3. Escolha **Postgres** ou **PostgreSQL**.
4. Nome sugerido: `linkflix-db`.
5. Selecione a mesma região do serviço web Linkflix.
6. Crie o banco e aguarde ficar disponível.
7. Na página do banco, localize **Internal Database URL** e copie o valor inteiro.

Use a URL interna. Ela é mais rápida e apropriada para o serviço que também está no Render.

## PARTE 3 — conectar o site ao banco

1. Abra o serviço web do Linkflix no Render.
2. Entre em **Environment**.
3. Clique em **Add Environment Variable**.
4. Nome da variável: `DATABASE_URL`.
5. Valor: cole a **Internal Database URL** copiada.
6. Salve as alterações.
7. O Render iniciará um novo deploy. Caso não inicie, use **Manual Deploy → Deploy latest commit**.

## PARTE 4 — primeira inicialização

No primeiro acesso após conectar o PostgreSQL, o Linkflix:

- cria automaticamente as tabelas;
- verifica se o PostgreSQL está vazio;
- copia os dados do `instance/linkflix.db` incluído no projeto;
- passa a salvar os novos cadastros diretamente no PostgreSQL.

A primeira abertura pode demorar alguns segundos. Não interrompa o deploy.

## PARTE 5 — teste obrigatório

1. Entre no painel Admin.
2. Adicione um filme de teste pelo TMDB.
3. Confirme que apareceu no site.
4. No Render, faça **Manual Deploy → Deploy latest commit**.
5. Depois do deploy, abra o site novamente.
6. O filme deve continuar cadastrado.

## Como confirmar que está usando PostgreSQL

Abra os logs do serviço web no Render. Não deve aparecer erro de conexão com `DATABASE_URL`.
Se o filme continuar depois de um novo deploy, a configuração está funcionando.

## Erros comuns

### O filme ainda desaparece

Normalmente significa que `DATABASE_URL` não foi adicionada ao **serviço web correto**, ou que foi copiada incompleta.
Confira o nome exato da variável e faça outro deploy.

### `connection refused`, timeout ou erro de DNS

Confira se o banco e o serviço web estão na mesma região e se você usou a **Internal Database URL**.

### O site mostra erro 500 após adicionar `DATABASE_URL`

Abra **Logs** no Render e procure a primeira linha vermelha. Verifique também se `psycopg2-binary` está no `requirements.txt` — ele já está incluído nesta versão.

### Avatares enviados por arquivo somem

O PostgreSQL salva o caminho do avatar, mas arquivos enviados para `static/uploads` ainda ficam no disco temporário do Render. Para avatares permanentes, use URL de imagem ou, futuramente, armazenamento como Cloudinary/S3. Isso não afeta filmes importados por URL/TMDB.

## Backup recomendado

Antes de grandes alterações, exporte um backup do PostgreSQL no painel do Render ou mantenha o arquivo `instance/linkflix.db` antigo guardado no computador durante a migração inicial.
