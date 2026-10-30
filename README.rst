Base Skeleton to start your application using Flask-AppBuilder
--------------------------------------------------------------

- Install it::

	pip install flask-appbuilder
	git clone https://github.com/dpgaspar/Flask-AppBuilder-Skeleton.git

- Run it::

    $ export FLASK_APP=app
    # Create an admin user
    $ flask fab create-admin
    # Run dev server
    $ flask run


That's it!!

Development
-----------

- Install the dependencies (including the dev ones) from ``Pipfile.lock``::

    pipenv install --dev

- Install the git hooks, once per clone. They run Ruff, the filesystem checks
  and the test suite on every commit::

    pre-commit install

- After changing ``Pipfile.lock``, regenerate the ``requirements.txt`` the
  Docker image installs from (CI checks the two match)::

    pipenv requirements > requirements.txt
